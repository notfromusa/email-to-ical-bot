"""
Web UI for monitoring iCal Bot
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from flask import Flask, Response, render_template, jsonify, request, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
from storage import StorageManager
from email_client import EmailClient
from exchange_client import ExchangeClient
from messaging import build_reply_message
from calendar_generator import CalendarGenerator
from llm_client import LLMClient
from main import iCalBot

app = Flask(__name__)
_auth_warning_logged = False


def _auth_store_path() -> Path:
    return Path(getattr(config, 'UI_AUTH_STORE_PATH', 'data/ui_auth.json'))


def _auth_secret_path() -> Path:
    return Path(getattr(config, 'UI_AUTH_SECRET_PATH', 'data/ui_auth_secret.key'))


def _auth_configured() -> bool:
    store = _auth_store_path()
    if not store.exists():
        return False
    try:
        payload = json.loads(store.read_text(encoding='utf-8'))
    except Exception:
        return False
    return bool(payload.get('password_hash'))


def _auth_requires_change() -> bool:
    payload = _load_auth()
    return bool(payload.get('password_hash')) and bool(payload.get('must_change'))


def _load_auth() -> dict:
    store = _auth_store_path()
    if not store.exists():
        return {}
    try:
        return json.loads(store.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_auth(username: str, password_hash: str) -> None:
    store = _auth_store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    payload = {'username': username, 'password_hash': password_hash, 'must_change': False}
    store.write_text(json.dumps(payload), encoding='utf-8')
    try:
        os.chmod(store, 0o600)
    except Exception:
        pass


def _check_auth() -> bool:
    return bool(session.get('authenticated'))


def _is_local_request() -> bool:
    remote_addr = request.remote_addr or ''
    if remote_addr not in {'127.0.0.1', '::1'}:
        return False
    # Treat proxied requests as non-local to avoid Host spoofing
    if request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP'):
        return False
    return True


def _is_secure_request() -> bool:
    if request.is_secure:
        return True
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    return forwarded_proto.lower() == 'https'


def _credentials_ready() -> bool:
    try:
        return config.has_email_password()
    except AttributeError:
        return bool(getattr(config, 'EMAIL_PASSWORD', ''))


def _ensure_secret_key() -> None:
    secret_path = _auth_secret_path()
    if secret_path.exists():
        app.secret_key = secret_path.read_text(encoding='utf-8').strip()
        return
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32).hex()
    secret_path.write_text(secret, encoding='utf-8')
    try:
        os.chmod(secret_path, 0o600)
    except Exception:
        pass
    app.secret_key = secret


@app.before_request
def enforce_auth():
    global _auth_warning_logged
    path = request.path or ''
    if getattr(config, 'REQUIRE_HTTPS_FOR_REMOTE', True) and not _is_local_request() and not _is_secure_request():
        return Response('HTTPS required', 400)
    if path.startswith('/setup') or path.startswith('/login'):
        return None
    if not _auth_configured():
        if not _is_local_request():
            return Response('Authentication required', 401)
        if not _auth_warning_logged:
            app.logger.warning("Web UI authentication is not configured; setup required.")
            _auth_warning_logged = True
        return redirect(url_for('setup'))
    if _auth_requires_change():
        return redirect(url_for('setup'))
    if _check_auth():
        return None
    if path.startswith('/api/'):
        return jsonify({'error': 'Authentication required'}), 401
    return redirect(url_for('login'))

# Global variable to hold bot instance
bot_instance = None
bot_thread = None
bot_running = False
storage = StorageManager(config.RESULTS_DB_PATH)
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / 'templates' / 'index.html'
try:
    DEFAULT_TEMPLATE = TEMPLATE_PATH.read_text(encoding='utf-8')
except FileNotFoundError:
    DEFAULT_TEMPLATE = """<!DOCTYPE html><html><body><p>Template missing. Please create templates/index.html.</p></body></html>"""
manual_check_lock = threading.Lock()
last_manual_check = None
last_manual_error = None

SUMMARY_HIDDEN_FIELDS = {
    'email_body',
    'ics_content',
    'llm_prompt',
    'llm_response',
    'llm_raw_result',
}

EPOCH_FALLBACK = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _default_timezone():
    timezone_name = (getattr(config, 'DEFAULT_TIMEZONE', '') or '').strip()
    if not timezone_name or timezone_name.upper() == 'UTC':
        timezone_name = 'Europe/Berlin'
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return timezone.utc


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_default_timezone())
    return dt.astimezone(timezone.utc)


def _resolve_event_datetime(record):
    if not record:
        return None
    for key in ('email_date', 'processed_at', 'event_start', 'approval_timestamp'):
        value = record.get(key)
        dt = _parse_iso_datetime(value)
        if dt:
            return dt
    return None


def _decode_recipient_list(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            raw = parsed
        else:
            return [addr.strip() for addr in raw.split(',') if addr.strip()]
    if isinstance(raw, (list, tuple, set)):
        recipients = []
        for addr in raw:
            addr_str = (addr or "").strip()
            if addr_str and addr_str not in recipients:
                recipients.append(addr_str)
        return recipients
    addr_str = str(raw).strip()
    return [addr_str] if addr_str else []


def _decode_event_candidates(raw):
    if raw is None:
        return []
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []

    candidates = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        candidate = {
            'title': item.get('title') or 'Untitled event',
            'description': item.get('description') or '',
            'start_datetime': item.get('start_datetime') or '',
            'end_datetime': item.get('end_datetime') or '',
            'location': item.get('location') or '',
        }
        if not candidate['start_datetime']:
            continue
        candidates.append(candidate)
    return candidates


def _normalize_record(record):
    normalized = dict(record)
    for key in ('has_event', 'sent', 'dry_run', 'approval_required', 'prompt_injection'):
        if key in normalized:
            normalized[key] = bool(normalized[key])

    candidates = _decode_event_candidates(normalized.get('event_candidates'))
    if not candidates and normalized.get('has_event'):
        fallback = {
            'title': normalized.get('event_title') or 'Untitled event',
            'description': normalized.get('event_description') or '',
            'start_datetime': normalized.get('event_start') or '',
            'end_datetime': normalized.get('event_end') or '',
            'location': normalized.get('event_location') or '',
        }
        if fallback['start_datetime']:
            candidates = [fallback]
    normalized['event_candidates'] = candidates

    try:
        selected_index = int(normalized.get('selected_event_index'))
    except (TypeError, ValueError):
        selected_index = 0
    if selected_index < 0:
        selected_index = 0
    if candidates and selected_index >= len(candidates):
        selected_index = 0
    normalized['selected_event_index'] = selected_index
    normalized['email_recipients'] = _decode_recipient_list(normalized.get('email_recipients'))
    sent_indices_raw = normalized.get('sent_event_indices')
    if isinstance(sent_indices_raw, str):
        try:
            normalized['sent_event_indices'] = json.loads(sent_indices_raw)
        except Exception:
            normalized['sent_event_indices'] = []
    elif isinstance(sent_indices_raw, list):
        normalized['sent_event_indices'] = sent_indices_raw
    else:
        normalized['sent_event_indices'] = []
    return normalized


def _summarize_record(record):
    data = _normalize_record(record)
    for field in SUMMARY_HIDDEN_FIELDS:
        data.pop(field, None)
    event_dt = _resolve_event_datetime(data)
    data['email_timestamp'] = event_dt.isoformat() if event_dt else None
    return data, event_dt


def _detailed_record(record):
    data = _normalize_record(record)
    raw_payload = data.get('llm_raw_result')
    if isinstance(raw_payload, str):
        try:
            data['llm_raw_result'] = json.loads(raw_payload)
        except json.JSONDecodeError:
            pass
    data['approval_history'] = storage.get_approval_history(record.get('email_id')) if record.get('email_id') else []
    return data


def _build_event_payload(record, selected_event_index=None):
    candidates = _decode_event_candidates(record.get('event_candidates'))
    if not candidates:
        return {
            'title': record.get('event_title') or 'Untitled event',
            'description': record.get('event_description') or '',
            'start_datetime': record.get('event_start') or '',
            'end_datetime': record.get('event_end') or '',
            'location': record.get('event_location') or '',
        }, 0

    if selected_event_index is None:
        selected_event_index = record.get('selected_event_index')
    try:
        index = int(selected_event_index)
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(candidates):
        index = 0
    return candidates[index], index


def _event_candidates_from_event_data(event_data):
    if not isinstance(event_data, dict):
        return []
    candidates = event_data.get('events')
    if not isinstance(candidates, list):
        candidates = []

    normalized = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate = {
            'title': item.get('title') or 'Untitled event',
            'description': item.get('description') or '',
            'start_datetime': item.get('start_datetime') or '',
            'end_datetime': item.get('end_datetime') or '',
            'location': item.get('location') or '',
        }
        if not candidate['start_datetime']:
            continue
        normalized.append(candidate)

    if normalized:
        return normalized

    # Backward-compatible fallback for single-event payload shape
    single = {
        'title': event_data.get('title') or 'Untitled event',
        'description': event_data.get('description') or '',
        'start_datetime': event_data.get('start_datetime') or '',
        'end_datetime': event_data.get('end_datetime') or '',
        'location': event_data.get('location') or '',
    }
    if single['start_datetime']:
        return [single]
    return []


def _format_send_result_note(result):
    if not result:
        return None
    parts = []
    recipients = result.get('recipients') or []
    if recipients:
        parts.append(f"Recipients: {', '.join(recipients)}")
    protocol = result.get('protocol')
    if protocol:
        parts.append(f"Protocol: {protocol}")
    message_id = result.get('message_id')
    if message_id:
        parts.append(f"Message-ID: {message_id}")
    return " | ".join(parts) if parts else None


def _send_calendar_invite_from_record(record, recipients_override=None, selected_event_index=None, event_overrides=None):
    mail_client = ExchangeClient() if config.EMAIL_PROTOCOL == "EXCHANGE" else EmailClient()
    recipients = _decode_recipient_list(recipients_override) if recipients_override else _decode_recipient_list(record.get('email_recipients'))
    if not recipients and record.get('email_sender'):
        recipients = [record['email_sender']]
    if not recipients:
        raise ValueError('No recipients available for this event')

    original_email = {
        'id': record.get('email_id'),
        'subject': record.get('email_subject'),
        'sender': record.get('email_sender'),
        'message_id': record.get('email_message_id') or record.get('email_id'),
        'recipients': recipients,
    }
    if hasattr(mail_client, 'get_message_by_id') and callable(getattr(mail_client, 'get_message_by_id')):
        original_msg = mail_client.get_message_by_id(original_email.get('message_id'))
        if original_msg:
            original_email['original_msg'] = original_msg

    event_payload, resolved_index = _build_event_payload(record, selected_event_index=selected_event_index)
    if event_overrides and isinstance(event_overrides, dict):
        for field in ('title', 'description', 'start_datetime', 'end_datetime', 'location'):
            if event_overrides.get(field) is not None:
                event_payload[field] = str(event_overrides[field]).strip()
    reply_message = build_reply_message(event_payload, recipients)

    generator = CalendarGenerator()
    ics_content = record.get('ics_content')
    try:
        ics_content = generator.create_ics_invite(event_payload, recipients=recipients)
    except Exception as exc:
        app.logger.warning(
            "Failed to regenerate ICS for %s: %s; using stored content",
            record.get('email_id'),
            exc,
        )
        if not ics_content:
            raise
    if not ics_content:
        raise ValueError('Missing ICS content for this event')

    return mail_client.send_reply_with_calendar(
        original_email,
        ics_content,
        reply_message,
        dry_run=config.DRY_RUN,
    ), event_payload, resolved_index, ics_content


def get_stats():
    """Get statistics about processed emails"""
    stats = storage.get_stats()
    stats.update(
        {
            'dry_run_mode': config.DRY_RUN,
            'check_interval': config.CHECK_INTERVAL_SECONDS,
            'bot_running': bot_running,
            'auto_approve_mode': config.AUTO_APPROVE_EVENTS,
            'manual_check_running': manual_check_lock.locked(),
            'last_manual_check': last_manual_check,
            'manual_check_error': last_manual_error,
        }
    )
    stats.setdefault('total_events', stats.get('events_detected', 0))
    return stats


@app.route('/')
def index():
    """Main dashboard"""
    return render_template('index.html')


@app.route('/api/events')
def api_events():
    """API endpoint for event history"""
    if not _credentials_ready():
        return jsonify({'error': 'Mailbox credentials are not configured'}), 409
    filter_type = request.args.get('filter', 'all')
    limit_param = request.args.get('limit', '200')
    try:
        limit = max(1, min(int(limit_param), 500))
    except ValueError:
        limit = 200
    summarized = [_summarize_record(entry) for entry in storage.get_results(filter_type, limit)]
    summarized.sort(
        key=lambda item: item[1] or EPOCH_FALLBACK,
        reverse=(filter_type != 'pending'),
    )
    history = [item[0] for item in summarized]
    return jsonify(history)


@app.route('/api/events/<email_id>')
def api_event_detail(email_id):
    if not _credentials_ready():
        return jsonify({'error': 'Mailbox credentials are not configured'}), 409
    record = storage.get_result(email_id)
    if not record:
        return jsonify({'error': 'Event not found'}), 404
    return jsonify(_detailed_record(record))


@app.route('/api/events/<email_id>/approve', methods=['POST'])
def api_approve_event(email_id):
    record = storage.get_result(email_id)
    if not record:
        return jsonify({'error': 'Event not found'}), 404
    if not record.get('has_event'):
        return jsonify({'error': 'No event data stored for this email'}), 400
    if not record.get('approval_required'):
        return jsonify({'error': 'This event does not require approval'}), 400
    available_candidates = _decode_event_candidates(record.get('event_candidates'))
    sent_indices = record.get('sent_event_indices') or []
    if isinstance(sent_indices, str):
        try:
            sent_indices = json.loads(sent_indices)
        except Exception:
            sent_indices = []

    request_payload = request.get_json(silent=True) or {} if request.is_json else {}
    approver = request_payload.get('approver')
    selected_event_index = request_payload.get('selected_event_index')
    if selected_event_index is not None:
        try:
            selected_event_index = int(selected_event_index)
        except (TypeError, ValueError):
            return jsonify({'error': 'selected_event_index must be an integer'}), 400

    if len(available_candidates) > 1 and selected_event_index is None:
        for idx in range(len(available_candidates)):
            if idx not in sent_indices:
                selected_event_index = idx
                break
        if selected_event_index is None:
            selected_event_index = 0

    is_resend = bool(record.get('sent')) or (selected_event_index is not None and selected_event_index in sent_indices)

    try:
        recipients_override = request_payload.get('recipients')

        event_overrides = request_payload.get('event_overrides')

        send_result, selected_event, resolved_index, ics_content = _send_calendar_invite_from_record(
            record,
            recipients_override=recipients_override,
            selected_event_index=selected_event_index,
            event_overrides=event_overrides,
        )
        if available_candidates and resolved_index < len(available_candidates):
            available_candidates[resolved_index] = dict(selected_event)
        note = _format_send_result_note(send_result)
        if is_resend:
            note = f"Re-sent: {note}" if note else "Re-sent invite"
        storage.mark_event_sent(
            email_id,
            status='dry_run_preview' if config.DRY_RUN else 'invite_sent',
            sent=not config.DRY_RUN,
            approver=approver,
            note=note,
            event_data=selected_event,
            event_candidates=available_candidates or None,
            selected_event_index=resolved_index,
            ics_content=ics_content,
            email_recipients=send_result.get('recipients') if send_result else None,
            action='resent' if is_resend else None,
        )
        action_verb = "Re-sent" if is_resend else "Approved and sent"
        if send_result and send_result.get('recipients'):
            app.logger.info(
                "%s invite for %s to %s",
                action_verb,
                email_id,
                ', '.join(send_result['recipients']),
            )
        else:
            app.logger.info("%s invite for %s", action_verb, email_id)
        return jsonify({'ok': True, 'send_result': send_result, 'is_resend': is_resend})
    except Exception as exc:
        storage.mark_event_sent(
            email_id,
            status='send_error',
            sent=False,
            approver=approver,
            error=str(exc),
        )
        app.logger.error("Failed to approve event %s: %s", email_id, exc)
        return jsonify({'error': f'Failed to send invite: {exc}'}), 500


@app.route('/api/events/<email_id>/decline', methods=['POST'])
def api_decline_event(email_id):
    record = storage.get_result(email_id)
    if not record:
        return jsonify({'error': 'Event not found'}), 404
    if not record.get('has_event'):
        return jsonify({'error': 'No event data stored for this email'}), 400
    if not record.get('approval_required'):
        return jsonify({'error': 'This event does not require approval'}), 400
    if record.get('approval_status') == 'declined':
        return jsonify({'error': 'This event has already been declined'}), 400
    if record.get('approval_status') == 'approved':
        return jsonify({'error': 'This event has already been approved'}), 400

    actor = None
    reason = None
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        actor = payload.get('approver') or payload.get('actor')
        reason = payload.get('reason')

    storage.mark_event_declined(email_id, actor=actor, note=reason)
    app.logger.info("Declined event %s", email_id)
    return jsonify({'ok': True})


@app.route('/api/events/<email_id>/rescan', methods=['POST'])
def api_rescan_event(email_id):
    """Re-fetch the email from the mailbox and re-run LLM event extraction."""
    if not _credentials_ready():
        return jsonify({'error': 'Mailbox credentials are not configured'}), 409

    record = storage.get_result(email_id)
    if not record:
        return jsonify({'error': 'Event not found'}), 404

    message_id = record.get('email_message_id')
    subject = record.get('email_subject')
    sender = record.get('email_sender')

    payload = request.get_json(silent=True) or {} if request.is_json else {}
    double_check = bool(payload.get('double_check', False))
    actor = payload.get('approver') or payload.get('actor') or 'User'

    mail_client = ExchangeClient() if config.EMAIL_PROTOCOL == "EXCHANGE" else EmailClient()
    try:
        email_data = mail_client.fetch_email_by_id(
            email_id=email_id,
            message_id=message_id,
            subject=subject,
            sender=sender,
        )
    except Exception as exc:
        app.logger.error("Error fetching email %s from mailbox: %s", email_id, exc)
        return jsonify({'error': f'Failed to retrieve email from mailbox: {exc}'}), 500

    if not email_data:
        return jsonify({'error': f'Could not locate this email in your mailbox inbox (ID: {email_id}). It may have been moved, deleted, or is older than the scan window.'}), 404

    try:
        llm_client = LLMClient()
        event_data, metadata = llm_client.analyze_email_for_event(
            email_data['body'],
            email_data['subject'],
            reference_timestamp=email_data.get('date'),
            double_check=double_check,
        )
    except Exception as exc:
        app.logger.error("LLM extraction failed during rescan for %s: %s", email_id, exc)
        return jsonify({'error': f'LLM extraction failed: {exc}'}), 500

    candidates = _event_candidates_from_event_data(event_data)
    has_event = bool(candidates)
    calendar_generator = CalendarGenerator()
    ics_preview = None
    ics_content = None

    if has_event:
        try:
            recipients = email_data.get('recipients') or ([email_data['sender']] if email_data.get('sender') else None)
            ics_content = calendar_generator.create_ics_invite(
                candidates[0],
                recipients=recipients,
            )
            if ics_content:
                ics_preview = ics_content[:500] + '...' if len(ics_content) > 500 else ics_content
        except Exception as exc:
            app.logger.warning("Failed to generate ICS during rescan for %s: %s", email_id, exc)

    storage.reset_for_rescan(email_id)
    storage.record_result(
        email_id=email_id,
        email_message_id=email_data.get('message_id') or message_id,
        email_subject=email_data.get('subject') or record.get('email_subject'),
        email_sender=email_data.get('sender') or record.get('email_sender'),
        email_date=email_data.get('date') or record.get('email_date'),
        has_event=has_event,
        event_data=candidates[0] if candidates else None,
        event_candidates=candidates or None,
        selected_event_index=0 if candidates else None,
        ics_preview=ics_preview,
        ics_content=ics_content,
        sent=False,
        dry_run=config.DRY_RUN,
        status='pending_approval' if has_event else 'no_event',
        approval_required=True if has_event else False,
        approval_status='pending' if has_event else None,
        email_recipients=email_data.get('recipients') or record.get('email_recipients'),
        llm_prompt=metadata.get('prompt'),
        llm_response=metadata.get('response_text'),
        llm_raw_result=metadata.get('raw_result'),
        prompt_injection=metadata.get('prompt_injection', False),
    )

    note_text = 'Rescanned email from mailbox'
    if double_check:
        note_text += ' with double-check QA loop'
    storage.log_approval_action(email_id, 'rescanned', actor=actor, note=note_text)

    updated_record = storage.get_result(email_id)
    return jsonify({
        'ok': True,
        'record': _detailed_record(updated_record) if updated_record else None,
        'message': f"Email successfully rescanned{' with double-check' if double_check else ''}."
    })


@app.route('/api/events/<email_id>/preview-ics', methods=['POST'])
def api_preview_ics(email_id):
    """Generate on-the-fly ICS content for edited event preview."""
    record = storage.get_result(email_id)
    if not record:
        return jsonify({'error': 'Event not found'}), 404
    payload = request.get_json(silent=True) or {} if request.is_json else {}
    selected_event_index = payload.get('selected_event_index')
    event_overrides = payload.get('event_overrides')
    recipients_override = payload.get('recipients')

    recipients = _decode_recipient_list(recipients_override) if recipients_override else _decode_recipient_list(record.get('email_recipients'))
    if not recipients and record.get('email_sender'):
        recipients = [record['email_sender']]

    event_payload, _ = _build_event_payload(record, selected_event_index=selected_event_index)
    if event_overrides and isinstance(event_overrides, dict):
        for field in ('title', 'description', 'start_datetime', 'end_datetime', 'location'):
            if event_overrides.get(field) is not None:
                event_payload[field] = str(event_overrides[field]).strip()

    generator = CalendarGenerator()
    try:
        ics_content = generator.create_ics_invite(event_payload, recipients=recipients)
        return jsonify({'ok': True, 'ics_content': ics_content})
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    if not _credentials_ready():
        return jsonify({'error': 'Mailbox credentials are not configured'}), 409
    return jsonify(get_stats())


@app.route('/api/config')
def api_config():
    """API endpoint for configuration"""
    return jsonify({
        'dry_run': config.DRY_RUN,
        'check_interval': config.CHECK_INTERVAL_SECONDS,
        'max_emails_per_check': config.MAX_EMAILS_PER_CHECK,
        'llm_model': config.LLM_MODEL,
        'email_address': config.EMAIL_ADDRESS,
        'internal_domain': getattr(config, 'INTERNAL_EMAIL_DOMAIN', None),
        'attachment_parsing_enabled': bool(getattr(config, 'ATTACHMENT_PARSING_ENABLED', True)),
    })


@app.route('/api/config/attachment-parsing', methods=['POST'])
def api_set_attachment_parsing():
    payload = request.get_json(silent=True) or {}
    if 'enabled' not in payload:
        return jsonify({'error': 'Missing enabled flag'}), 400

    enabled = bool(payload.get('enabled'))
    config.set_attachment_parsing_enabled(enabled, persist=True)
    app.logger.info("Attachment parsing %s via UI", "enabled" if enabled else "disabled")
    return jsonify({'ok': True, 'attachment_parsing_enabled': bool(config.ATTACHMENT_PARSING_ENABLED)})


@app.route('/api/credentials/status')
def api_credentials_status():
    return jsonify({'configured': _credentials_ready()})


@app.route('/api/credentials', methods=['POST'])
def api_set_credentials():
    if getattr(config, 'REQUIRE_HTTPS_FOR_CREDENTIALS', True) and not (_is_secure_request() or _is_local_request()):
        return jsonify({'error': 'TLS required for credential submission'}), 400

    payload = None
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    if payload is None:
        payload = request.form or {}

    password = (payload.get('password') or '').strip()
    email_address = (payload.get('email_address') or '').strip()
    exchange_username = (payload.get('exchange_username') or '').strip()

    if not password:
        return jsonify({'error': 'Password is required'}), 400

    try:
        config.set_email_credentials(
            password,
            email_address=email_address or None,
            exchange_username=exchange_username or None,
        )
    except Exception:
        config.EMAIL_PASSWORD = password
        if email_address:
            config.EMAIL_ADDRESS = email_address
            config.EXCHANGE_EMAIL = email_address
        if exchange_username:
            config.EXCHANGE_USERNAME = exchange_username

    return jsonify({'ok': True})


@app.route('/api/database/censor', methods=['POST'])
def api_censor_database():
    try:
        result = storage.censor_sensitive_fields()
        return jsonify({'ok': True, **result})
    except Exception as exc:
        app.logger.error("Database censoring failed: %s", exc)
        return jsonify({'error': f'Failed to censor database: {exc}'}), 500


@app.route('/api/database/purge', methods=['POST'])
def api_purge_database():
    try:
        result = storage.purge_old_entries(days=7)
        return jsonify({'ok': True, **result})
    except Exception as exc:
        app.logger.error("Database purge failed: %s", exc)
        return jsonify({'error': f'Failed to purge database: {exc}'}), 500


@app.route('/api/database/repair', methods=['POST'])
def api_repair_database():
    try:
        result = storage.reconcile_approvals()
        return jsonify({'ok': True, **result})
    except Exception as exc:
        app.logger.error("Database repair failed: %s", exc)
        return jsonify({'error': f'Failed to repair database: {exc}'}), 500


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if not _is_local_request():
        return Response('Forbidden', 403)
    if _auth_configured() and not _auth_requires_change():
        return redirect(url_for('index'))
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        if not username or not password:
            error = 'Username and password are required.'
        else:
            password_hash = generate_password_hash(password)
            _save_auth(username, password_hash)
            session['authenticated'] = True
            session['username'] = username
            return redirect(url_for('index'))
    return render_template('setup.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not _auth_configured():
        return redirect(url_for('setup'))
    if _auth_requires_change():
        return redirect(url_for('setup'))
    error = None
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        auth = _load_auth()
        if not username or not password:
            error = 'Username and password are required.'
        elif username != auth.get('username'):
            error = 'Invalid username or password.'
        elif not check_password_hash(auth.get('password_hash', ''), password):
            error = 'Invalid username or password.'
        else:
            session['authenticated'] = True
            session['username'] = username
            return redirect(url_for('index'))
    return render_template('login.html', error=error)


def _start_manual_check():
    global bot_running, last_manual_check, last_manual_error
    if not _credentials_ready():
        return False, "Mailbox credentials are not configured"
    if manual_check_lock.locked():
        return False, "Email check already in progress"
    acquired = manual_check_lock.acquire(blocking=False)
    if not acquired:
        return False, "Email check already in progress"

    def worker():
        global bot_running, last_manual_check, last_manual_error
        bot_running = True
        try:
            bot = iCalBot()
            bot.run_once()
            last_manual_check = datetime.utcnow().isoformat()
            last_manual_error = None
        except Exception as exc:
            last_manual_error = str(exc)
            app.logger.error("Manual email check failed: %s", exc)
        finally:
            bot_running = False
            manual_check_lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return True, None


@app.route('/api/check-now', methods=['POST'])
def api_check_now():
    ok, error = _start_manual_check()
    if not ok:
        return jsonify({'error': error}), 409
    return jsonify({'ok': True})


@app.route('/api/manual-evaluate', methods=['POST'])
def api_manual_evaluate():
    payload = request.get_json(silent=True) or {}
    subject = (payload.get('subject') or '').strip() or '(Manual evaluation)'
    body = (payload.get('body') or '').strip()
    reference_timestamp = (payload.get('reference_timestamp') or '').strip() or None

    if not body:
        return jsonify({'error': 'Email text is required'}), 400

    llm_client = LLMClient()
    generator = CalendarGenerator()

    event_data, metadata = llm_client.analyze_email_for_event(
        body,
        subject,
        reference_timestamp=reference_timestamp,
    )

    candidates = _event_candidates_from_event_data(event_data or {})
    response_payload = {
        'ok': True,
        'subject': subject,
        'has_event': bool(event_data and event_data.get('has_event') and candidates),
        'events': candidates,
        'selected_event': None,
        'selected_event_index': None,
        'ics_content': None,
        'ics_error': None,
        'llm_prompt': metadata.get('prompt'),
        'llm_response': metadata.get('response_text'),
        'llm_raw_result': iCalBot._sanitize_llm_payload(metadata.get('raw_result')),
        'prompt_injection': bool(metadata.get('prompt_injection')),
    }

    if not response_payload['has_event']:
        return jsonify(response_payload)

    selected_event = None
    selected_event_index = None
    for idx, candidate in enumerate(candidates):
        if generator.validate_event_data(candidate):
            selected_event = candidate
            selected_event_index = idx
            break

    if not selected_event:
        response_payload['ics_error'] = 'No valid future event candidate available for ICS generation.'
        return jsonify(response_payload)

    response_payload['selected_event'] = selected_event
    response_payload['selected_event_index'] = selected_event_index

    recipients = []
    self_address = (getattr(config, 'EMAIL_ADDRESS', '') or '').strip()
    if self_address:
        recipients.append(self_address)

    try:
        response_payload['ics_content'] = generator.create_ics_invite(selected_event, recipients=recipients or None)
    except Exception as exc:
        response_payload['ics_error'] = str(exc)

    return jsonify(response_payload)


@app.route('/api/manual-evaluate/send-self', methods=['POST'])
def api_manual_evaluate_send_self():
    if not _credentials_ready():
        return jsonify({'error': 'Mailbox credentials are not configured'}), 409

    payload = request.get_json(silent=True) or {}
    subject = (payload.get('subject') or '').strip() or '(Manual evaluation)'
    ics_content = (payload.get('ics_content') or '').strip()
    if not ics_content:
        return jsonify({'error': 'ICS content is required'}), 400

    self_address = (getattr(config, 'EMAIL_ADDRESS', '') or '').strip()
    if not self_address:
        return jsonify({'error': 'Configured EMAIL_ADDRESS is required'}), 400

    selected_event = payload.get('selected_event')
    event_payload = selected_event if isinstance(selected_event, dict) else payload.get('event')
    if not isinstance(event_payload, dict):
        event_payload = {}

    normalized_event = {
        'title': event_payload.get('title') or subject,
        'description': event_payload.get('description') or '',
        'start_datetime': event_payload.get('start_datetime') or '',
        'end_datetime': event_payload.get('end_datetime') or '',
        'location': event_payload.get('location') or '',
    }

    if normalized_event.get('start_datetime'):
        reply_message = build_reply_message(normalized_event, [self_address])
    else:
        reply_message = 'I generated the attached calendar invite from manual evaluation.'

    mail_client = ExchangeClient() if config.EMAIL_PROTOCOL == "EXCHANGE" else EmailClient()
    original_email = {
        'id': f"manual-eval-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
        'subject': subject,
        'sender': self_address,
        'message_id': None,
        'recipients': [self_address],
    }

    try:
        send_result = mail_client.send_reply_with_calendar(
            original_email,
            ics_content,
            reply_message,
            dry_run=config.DRY_RUN,
        )
        return jsonify({'ok': True, 'send_result': send_result})
    except Exception as exc:
        app.logger.error("Failed to send manual evaluation ICS to self: %s", exc)
        return jsonify({'error': f'Failed to send invite: {exc}'}), 500


def create_templates():
    """Ensure the HTML template exists."""
    TEMPLATE_PATH.parent.mkdir(exist_ok=True)
    if not TEMPLATE_PATH.exists():
        TEMPLATE_PATH.write_text(DEFAULT_TEMPLATE, encoding='utf-8')


def run_web_ui():
    """Start the web UI server"""
    create_templates()
    _ensure_secret_key()
    try:
        result = storage.censor_sensitive_fields()
        app.logger.info("Startup censor complete: %s record(s) updated", result.get('records_updated', 0))
    except Exception as exc:
        app.logger.warning("Startup censor failed: %s", exc)
    try:
        purge_result = storage.purge_old_entries(days=30)
        app.logger.info("Startup purge complete: %s record(s) deleted", purge_result.get('records_deleted', 0))
    except Exception as exc:
        app.logger.warning("Startup purge failed: %s", exc)
    app.run(host=config.WEB_UI_HOST, port=config.WEB_UI_PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    run_web_ui()
