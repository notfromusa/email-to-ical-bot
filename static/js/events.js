/**
 * Event card rendering, detail inspection modal, recipient picker, rescan, and approval actions.
 */

import { postJsonWithCsrf } from './api.js';
import { isPending } from './calendar.js';
import { escapeHtml, formatDate, renderIcsPreview } from './ics.js';

let currentDetail = null;
let currentModalRecipients = [];
let manualRecipients = new Set();
let currentCheckedRecipients = new Set();
let previewDebounceTimer = null;

let hooks = {
    getConfig: () => ({}),
    refreshFeedAndStats: () => {},
};

/**
 * Configure external callbacks needed by the event detail modal workflow.
 * @param {{getConfig: () => Record<string, any>, refreshFeedAndStats: () => void}} callbacks
 */
export function initEventsWorkflow(callbacks) {
    hooks = callbacks;
}

/**
 * Return the active detail record currently open in the modal.
 * @returns {Record<string, any>|null}
 */
export function getCurrentDetail() {
    return currentDetail;
}

/**
 * Format an audit log action name into title case.
 * @param {string} action Raw audit action identifier.
 * @returns {string}
 */
export function formatActionLabel(action) {
    if (!action) return 'Unknown';
    return action
        .replace(/_/g, ' ')
        .toLowerCase()
        .replace(/(^|\s)\w/g, char => char.toUpperCase());
}

/**
 * Render approval decision log entries for the detail modal.
 * @param {Array<Record<string, any>>} history Audit entries.
 * @returns {string} HTML markup string.
 */
export function renderDecisionHistory(history) {
    if (!Array.isArray(history) || history.length === 0) {
        return '<p class="detail-hint">No decision log yet.</p>';
    }
    return history.map(entry => {
        const label = formatActionLabel(entry.action);
        const metaParts = [];
        if (entry.timestamp) {
            metaParts.push(formatDate(entry.timestamp));
        }
        if (entry.actor) {
            metaParts.push(entry.actor);
        }
        const meta = metaParts.join(' · ');
        const note = entry.note ? `<p>${escapeHtml(entry.note)}</p>` : '';
        return `
            <div class="decision-log__item">
                <div class="decision-log__title">${escapeHtml(label)}</div>
                <div class="decision-log__meta">${escapeHtml(meta)}</div>
                ${note}
            </div>
        `;
    }).join('');
}

/**
 * Render an event feed card with all untrusted email/LLM fields HTML-escaped.
 * @param {Record<string, any>} event Summarized processed email record.
 * @returns {string} Escaped card HTML markup.
 */
export function renderEventCard(event) {
    const hasEvent = Boolean(event.has_event);
    const isLlmError = event.status === 'llm_error';
    const pendingApproval = isPending(event);
    const promptInjection = Boolean(event.prompt_injection);
    const candidateCount = Array.isArray(event.event_candidates) ? event.event_candidates.length : 0;
    const statusClass = isLlmError ? 'warn' : (pendingApproval ? 'info' : (hasEvent ? 'success' : 'info'));
    let statusLabel = event.status || (hasEvent ? 'Event detected' : 'No event detected');
    if (isLlmError) {
        statusLabel = '⚠️ LLM failed · Retry available';
    } else if (event.status === 'send_error') {
        statusLabel = 'Send failed · Retry available';
    } else if (pendingApproval) {
        statusLabel = 'Awaiting approval';
    } else if (hasEvent && event.sent) {
        statusLabel = 'Invite sent';
    } else if (hasEvent && !event.sent && event.dry_run) {
        statusLabel = 'Dry-run preview';
    }
    if (hasEvent && candidateCount > 1) {
        statusLabel = `${statusLabel} · ${candidateCount} candidates`;
    }

    const extraMeta = [];
    if (event.email_sender) {
        extraMeta.push(`<span><strong>Sender</strong><br>${escapeHtml(event.email_sender)}</span>`);
    }
    if (event.email_subject) {
        extraMeta.push(`<span><strong>Subject</strong><br>${escapeHtml(event.email_subject)}</span>`);
    }
    if (hasEvent && event.event_start) {
        extraMeta.push(`<span><strong>Start</strong><br>${escapeHtml(formatDate(event.event_start))}</span>`);
    }
    if (hasEvent && event.event_end) {
        extraMeta.push(`<span><strong>End</strong><br>${escapeHtml(formatDate(event.event_end))}</span>`);
    }
    if (hasEvent && event.event_location) {
        extraMeta.push(`<span><strong>Location</strong><br>${escapeHtml(event.event_location)}</span>`);
    }
    if (event.error) {
        extraMeta.push(`<span><strong>Error</strong><br>${escapeHtml(event.error)}</span>`);
    }

    const cardTitle = isLlmError
        ? '⚠️ LLM Extraction Failed'
        : (hasEvent ? (event.event_title || 'Untitled event') : 'No event extracted');
    const cardDesc = isLlmError
        ? (event.error ? `Error: ${event.error}` : 'The LLM call encountered an error or timeout during processing.')
        : (hasEvent ? (event.event_description || 'No description provided.') : 'The LLM did not detect a calendar-worthy event.');
    const safeEmailId = escapeHtml(event.email_id || '');

    return `
        <article class="event-card ${isLlmError ? 'event-card--warn' : (hasEvent ? 'event-card--invite' : 'event-card--info')}" data-email-id="${safeEmailId}">
            <h3>${escapeHtml(cardTitle)}</h3>
            <p>${escapeHtml(cardDesc)}</p>
            <span class="status-tag ${escapeHtml(statusClass)}">${escapeHtml(statusLabel)}</span>
            ${promptInjection ? '<span class="status-tag info">Prompt injection flagged</span>' : ''}
            <div class="event-meta">${extraMeta.join('')}</div>
            <div class="card-actions">
                <button class="pill-button" type="button" data-action="open-details" data-email-id="${safeEmailId}">Details</button>
                ${pendingApproval ? `<button class="pill-button accent" type="button" data-action="review-details" data-email-id="${safeEmailId}" data-focus="true">Review &amp; Decide</button>` : ''}
            </div>
        </article>
    `;
}

/**
 * Sync selected recipient checkboxes and trigger a debounced ICS preview refresh.
 */
export function onRecipientCheckChanged() {
    currentCheckedRecipients.clear();
    document.querySelectorAll('.recipient-checkbox').forEach(cb => {
        if (cb.checked) {
            currentCheckedRecipients.add(cb.value);
        }
    });
    triggerIcsPreviewUpdate();
}

/**
 * Add a manually entered recipient address to the current modal state.
 */
export function addManualRecipient() {
    const input = document.getElementById('new-recipient-input');
    if (!input) return;
    const addr = input.value.trim();
    if (!addr) return;
    if (!addr.includes('@') || !addr.includes('.') || addr.length < 5) {
        alert('Please enter a valid email address.');
        return;
    }
    if (!currentModalRecipients.includes(addr)) {
        currentModalRecipients.push(addr);
        manualRecipients.add(addr);
        currentCheckedRecipients.add(addr);
    } else {
        currentCheckedRecipients.add(addr);
    }
    input.value = '';
    renderRecipientPicker(currentModalRecipients, true);
    triggerIcsPreviewUpdate();
}

/**
 * Remove a manually added recipient from the detail modal state.
 * @param {string} addr Recipient email address.
 * @param {Event} [e] Optional DOM event.
 */
export function removeManualRecipient(addr, e) {
    if (e) e.stopPropagation();
    currentModalRecipients = currentModalRecipients.filter(a => a !== addr);
    manualRecipients.delete(addr);
    currentCheckedRecipients.delete(addr);
    renderRecipientPicker(currentModalRecipients, true);
    triggerIcsPreviewUpdate();
}

/**
 * Render recipient checkboxes, domain policy warning, and manual add bar.
 * @param {string[]} recipients Initial recipient list.
 * @param {boolean} [retainState=false] Keep existing selection state when re-rendering.
 */
export function renderRecipientPicker(recipients, retainState = false) {
    const container = document.getElementById('recipient-picker');
    if (!container) return;
    const cfg = hooks.getConfig() || {};
    const allowedDomain = (cfg.internal_domain || '').toLowerCase();

    if (!retainState) {
        currentModalRecipients = Array.isArray(recipients) ? Array.from(new Set(recipients.filter(Boolean))) : [];
        manualRecipients.clear();
        currentCheckedRecipients = new Set(
            currentModalRecipients.filter(addr => (allowedDomain ? addr.toLowerCase().endsWith(`@${allowedDomain}`) : true)),
        );
    }

    const list = currentModalRecipients;
    let pickerHtml = '';
    if (list.length) {
        pickerHtml = list.map((addr, idx) => {
            const isChecked = currentCheckedRecipients.has(addr);
            const isManual = manualRecipients.has(addr);
            return `
                <label class="recipient-pill" for="recip-${idx}">
                    <input type="checkbox" class="recipient-checkbox" id="recip-${idx}" value="${escapeHtml(addr)}" ${isChecked ? 'checked' : ''}>
                    <span>${escapeHtml(addr)}</span>
                    ${isManual ? `<button type="button" class="recipient-remove-btn" data-recipient-addr="${escapeHtml(addr)}" title="Remove recipient">&times;</button>` : ''}
                </label>
            `;
        }).join('');
    } else {
        pickerHtml = '<p class="detail-hint">No recipients detected.</p>';
    }

    let warning = '';
    if (allowedDomain) {
        const outside = list.filter(a => !a.toLowerCase().endsWith(`@${allowedDomain}`));
        if (outside.length) {
            warning = `<p class="detail-hint">Warning: Addresses outside ${escapeHtml(allowedDomain)} are unchecked by default.</p>`;
        }
    }

    const addBarHtml = `
        <div class="recipient-add-bar">
            <input type="email" id="new-recipient-input" placeholder="Add email address..." class="detail-editor-input recipient-add-input">
            <button type="button" class="pill-button recipient-add-btn" id="add-recipient-btn">+ Add</button>
        </div>
    `;

    container.innerHTML = pickerHtml + warning + addBarHtml;
}

/**
 * Populate the event candidate selector dropdown when candidates exist.
 * @param {Array<Record<string, any>>} candidates Event candidate array.
 * @param {number} selectedIndex Selected candidate index.
 * @param {boolean} editable Whether candidates can be approved/edited.
 */
export function renderEventCandidates(candidates, selectedIndex, editable) {
    const card = document.getElementById('event-candidate-card');
    const select = document.getElementById('event-candidate-select');
    const hint = document.getElementById('event-candidate-hint');
    if (!card || !select || !hint) return;

    const list = Array.isArray(candidates) ? candidates : [];
    if (!list.length) {
        card.classList.add('hidden');
        select.innerHTML = '';
        hint.textContent = '';
        return;
    }

    const sentIndices = Array.isArray(currentDetail?.sent_event_indices) ? currentDetail.sent_event_indices : [];
    let index = Number.isFinite(Number(selectedIndex)) ? Number(selectedIndex) : 0;
    if (index < 0 || index >= list.length) index = 0;

    if (sentIndices.includes(index)) {
        for (let i = 0; i < list.length; i++) {
            if (!sentIndices.includes(i)) {
                index = i;
                break;
            }
        }
    }

    select.innerHTML = list.map((candidate, idx) => {
        const isSent = sentIndices.includes(idx);
        const title = candidate?.title || `Event ${idx + 1}`;
        const start = candidate?.start_datetime ? formatDate(candidate.start_datetime) : 'No start time';
        const tag = isSent ? '✓ [SENT] ' : '';
        return `<option value="${idx}" class="${isSent ? 'option-sent' : ''}">${escapeHtml(tag + title)} · ${escapeHtml(start)}</option>`;
    }).join('');
    select.value = String(index);

    const multiple = list.length > 1;
    const allSent = multiple && sentIndices.length >= list.length;
    select.disabled = !editable && allSent;
    if (multiple && editable && !allSent) {
        const remaining = list.length - sentIndices.length;
        hint.textContent = sentIndices.length > 0
            ? `${sentIndices.length} of ${list.length} events sent (${remaining} remaining). Select event candidate to review or send.`
            : 'Select the event to approve and send.';
    } else if (allSent) {
        hint.textContent = `All ${list.length} event candidates have been sent.`;
    } else if (multiple) {
        hint.textContent = 'Multiple candidates detected. Selection is locked because this record is already finalized.';
    } else {
        hint.textContent = 'Single event candidate extracted.';
    }

    card.classList.remove('hidden');
}

/**
 * Read edited event field values from the detail modal editor inputs.
 * @returns {Record<string, string>}
 */
export function getEditorValues() {
    const title = document.getElementById('edit-event-title')?.value.trim() || '';
    const start = document.getElementById('edit-event-start')?.value.trim() || '';
    const end = document.getElementById('edit-event-end')?.value.trim() || '';
    const loc = document.getElementById('edit-event-location')?.value.trim() || '';
    const desc = document.getElementById('edit-event-description')?.value.trim() || '';
    return {
        title,
        start_datetime: start,
        end_datetime: end,
        location: loc,
        description: desc,
    };
}

/**
 * Populate or hide the inline event editor inside the detail modal.
 * @param {Record<string, any>|null} candidate Selected candidate or record fallback.
 * @param {boolean} _canEdit Reserved flag.
 * @param {boolean} [isSent=false] Whether this candidate was previously dispatched.
 */
export function populateEventEditor(candidate, _canEdit, isSent = false) {
    const editor = document.getElementById('detail-event-editor');
    if (!editor) return;
    if (!candidate) {
        editor.classList.add('hidden');
        return;
    }
    editor.classList.remove('hidden');
    const badge = document.getElementById('detail-edit-badge');
    if (badge) {
        badge.textContent = isSent ? 'Editable · Previously sent' : 'Editable preview';
        badge.className = 'status-tag info detail-event-editor__badge';
    }
    const titleInput = document.getElementById('edit-event-title');
    const startInput = document.getElementById('edit-event-start');
    const endInput = document.getElementById('edit-event-end');
    const locInput = document.getElementById('edit-event-location');
    const descInput = document.getElementById('edit-event-description');

    if (titleInput) { titleInput.value = candidate.title || candidate.event_title || ''; titleInput.disabled = false; }
    if (startInput) { startInput.value = candidate.start_datetime || candidate.event_start || ''; startInput.disabled = false; }
    if (endInput) { endInput.value = candidate.end_datetime || candidate.event_end || ''; endInput.disabled = false; }
    if (locInput) { locInput.value = candidate.location || candidate.event_location || ''; locInput.disabled = false; }
    if (descInput) { descInput.value = candidate.description || candidate.event_description || ''; descInput.disabled = false; }
}

/**
 * Request an updated server-generated ICS preview after editor input changes.
 */
export function triggerIcsPreviewUpdate() {
    clearTimeout(previewDebounceTimer);
    previewDebounceTimer = setTimeout(() => {
        if (!currentDetail) return;
        const candidateSelect = document.getElementById('event-candidate-select');
        const selectedIndex = candidateSelect && candidateSelect.value !== '' ? Number(candidateSelect.value) : 0;
        const overrides = getEditorValues();
        const selectedRecipients = Array.from(document.querySelectorAll('.recipient-checkbox'))
            .filter(cb => cb.checked)
            .map(cb => cb.value);

        postJsonWithCsrf(`/api/events/${encodeURIComponent(currentDetail.email_id)}/preview-ics`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                selected_event_index: selectedIndex,
                event_overrides: overrides,
                recipients: selectedRecipients,
            }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.ok && data.ics_content) {
                    renderIcsPreview(null, 'detail-ics', data.ics_content);
                }
            })
            .catch(() => {});
    }, 350);
}

/**
 * Load full event details from `/api/events/<id>` and open the modal dialog.
 * @param {string} emailId Target email identifier.
 * @param {boolean} [focusApproval=false] Focus approval controls once opened.
 */
export function openEventDetails(emailId, focusApproval = false) {
    if (!emailId) return;
    fetch(`/api/events/${encodeURIComponent(emailId)}`)
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Failed to load event details');
            }
            return payload;
        })
        .then(record => {
            currentDetail = record;
            const canDecide = isPending(record);
            const injectionWarning = document.getElementById('detail-injection-warning');
            if (injectionWarning) {
                injectionWarning.classList.toggle('hidden', !record.prompt_injection);
            }
            const errorBanner = document.getElementById('detail-error-banner');
            const errorText = document.getElementById('detail-error-text');
            if (errorBanner && errorText) {
                if (record.status === 'llm_error') {
                    errorText.textContent = `LLM extraction failed: ${record.error || 'The model timed out or encountered an error'}. You can trigger a rescan below.`;
                    errorBanner.classList.remove('hidden');
                } else if (record.status === 'send_error' || record.error) {
                    errorText.textContent = `Send failed: ${record.error || 'Unknown error'}. You can retry approving and sending.`;
                    errorBanner.classList.remove('hidden');
                } else {
                    errorBanner.classList.add('hidden');
                }
            }
            document.getElementById('detail-title').textContent = record.event_title || 'Event details';
            document.getElementById('detail-subject').textContent = record.email_subject || '—';
            document.getElementById('detail-sender').textContent = record.email_sender || '—';
            const recipients = Array.isArray(record.email_recipients) && record.email_recipients.length
                ? record.email_recipients
                : [record.email_sender].filter(Boolean);
            renderRecipientPicker(recipients);
            renderEventCandidates(record.event_candidates || [], record.selected_event_index || 0, canDecide);

            const doubleCheckCb = document.getElementById('rescan-double-check');
            if (doubleCheckCb) doubleCheckCb.checked = false;
            const rescanHintInput = document.getElementById('rescan-hint');
            if (rescanHintInput) rescanHintInput.value = '';

            const candidates = Array.isArray(record.event_candidates) ? record.event_candidates : [];
            const candSelect = document.getElementById('event-candidate-select');
            const selectedIdx = candSelect && candSelect.value !== ''
                ? Number(candSelect.value)
                : Number(record.selected_event_index || 0);
            const currentCand = candidates[selectedIdx] || (record.has_event ? record : null);
            const sentIndices = Array.isArray(record.sent_event_indices) ? record.sent_event_indices : [];
            const isCandidateSent = sentIndices.includes(selectedIdx);
            const isAlreadySent = Boolean(record.sent) || isCandidateSent;
            populateEventEditor(currentCand, true, isAlreadySent);

            document.getElementById('detail-status').textContent = record.status || (record.has_event ? 'Detected' : 'No event');
            document.getElementById('detail-llm-prompt').textContent = record.llm_prompt || '(prompt unavailable)';
            const llmResponse = typeof record.llm_raw_result === 'object'
                ? JSON.stringify(record.llm_raw_result, null, 2)
                : (record.llm_response || '');
            document.getElementById('detail-llm-response').textContent = llmResponse || '(response unavailable)';
            renderIcsPreview(null, 'detail-ics', record.ics_content || '', 'No ICS stored for this event.');

            const historyContainer = document.getElementById('detail-approval-log');
            historyContainer.innerHTML = renderDecisionHistory(record.approval_history || []);
            const approveBtn = document.getElementById('detail-approve-btn');
            const declineBtn = document.getElementById('detail-decline-btn');
            const approverInput = document.getElementById('approver-input');
            const noteInput = document.getElementById('decision-note-input');
            approverInput.value = '';
            noteInput.value = '';
            const hasEvent = Boolean(record.has_event);
            approveBtn.classList.toggle('hidden', !hasEvent);
            approveBtn.textContent = isAlreadySent ? '🔄 Re-send Invite' : 'Approve & Send';
            declineBtn.classList.toggle('hidden', !(canDecide && !isAlreadySent));
            approverInput.classList.toggle('hidden', !hasEvent);
            noteInput.classList.toggle('hidden', !hasEvent);

            const modal = document.getElementById('details-modal');
            if (modal) {
                modal.classList.add('open');
            }
            if (focusApproval && canDecide) {
                const candidateSelect = document.getElementById('event-candidate-select');
                if (candidateSelect && !candidateSelect.disabled) {
                    candidateSelect.focus();
                } else {
                    approveBtn.focus();
                }
            }
        })
        .catch(err => {
            alert(`Failed to load event details: ${err.message}`);
        });
}

/**
 * Reset modal controls and hide the event detail dialog.
 */
export function closeDetails() {
    try {
        currentDetail = null;
        currentModalRecipients = [];
        manualRecipients.clear();
        currentCheckedRecipients.clear();
        const approver = document.getElementById('approver-input');
        if (approver) approver.value = '';
        const note = document.getElementById('decision-note-input');
        if (note) note.value = '';
        const errorBanner = document.getElementById('detail-error-banner');
        if (errorBanner) errorBanner.classList.add('hidden');
        const picker = document.getElementById('recipient-picker');
        if (picker) picker.innerHTML = '';
        const candidateCard = document.getElementById('event-candidate-card');
        const candidateSelect = document.getElementById('event-candidate-select');
        const candidateHint = document.getElementById('event-candidate-hint');
        if (candidateCard) candidateCard.classList.add('hidden');
        if (candidateSelect) candidateSelect.innerHTML = '';
        if (candidateHint) candidateHint.textContent = '';
        populateEventEditor(null, false);
        const rawIcs = document.getElementById('detail-ics');
        if (rawIcs) rawIcs.textContent = '';
    } catch (err) {
        console.warn('Error during closeDetails:', err);
    } finally {
        const modal = document.getElementById('details-modal');
        if (modal) {
            modal.classList.remove('open');
        }
    }
}

/**
 * Collect decision form fields from the open modal.
 * @returns {Record<string, any>}
 */
export function getDecisionPayload() {
    const selectedRecipients = Array.from(document.querySelectorAll('.recipient-checkbox'))
        .filter(cb => cb.checked)
        .map(cb => cb.value)
        .filter(Boolean);
    const candidateSelect = document.getElementById('event-candidate-select');
    const selectedEventIndex = candidateSelect && candidateSelect.value !== ''
        ? Number(candidateSelect.value)
        : null;
    return {
        approver: document.getElementById('approver-input').value.trim(),
        reason: document.getElementById('decision-note-input').value.trim(),
        recipients: selectedRecipients,
        selected_event_index: Number.isFinite(selectedEventIndex) ? selectedEventIndex : null,
        event_overrides: getEditorValues(),
    };
}

/**
 * Serialize clean decision fields into a JSON string body.
 * @param {Record<string, any>} [payload={}]
 * @returns {string}
 */
export function cleanDecisionPayload(payload = {}) {
    const body = {};
    if (payload.approver) {
        body.approver = payload.approver;
    }
    if (payload.reason) {
        body.reason = payload.reason;
    }
    if (Array.isArray(payload.recipients) && payload.recipients.length) {
        body.recipients = payload.recipients;
    }
    if (Number.isFinite(payload.selected_event_index)) {
        body.selected_event_index = payload.selected_event_index;
    }
    if (payload.event_overrides && typeof payload.event_overrides === 'object') {
        body.event_overrides = payload.event_overrides;
    }
    return JSON.stringify(body);
}

function handleDecisionResult(message) {
    closeDetails();
    hooks.refreshFeedAndStats();
    alert(message);
}

/**
 * Submit an approve request for an event record.
 * @param {string} emailId Target email identifier.
 * @param {Record<string, any>} [payload={}] Decision payload.
 */
export function approveEvent(emailId, payload = {}) {
    const body = cleanDecisionPayload(payload);
    const approveBtn = document.getElementById('detail-approve-btn');
    if (approveBtn) {
        approveBtn.disabled = true;
        approveBtn.classList.add('loading');
    }
    postJsonWithCsrf(`/api/events/${encodeURIComponent(emailId)}/approve`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body,
    })
        .then(async response => {
            const resPayload = await response.json();
            if (!response.ok) {
                throw new Error(resPayload.error || 'Approval failed');
            }
            return resPayload;
        })
        .then(resPayload => {
            const cfg = hooks.getConfig() || {};
            const isResend = resPayload && resPayload.is_resend;
            const message = cfg.dry_run
                ? 'Dry-run preview stored (no email sent).'
                : (isResend ? 'Invite re-sent successfully.' : 'Invite queued with disclaimer.');
            handleDecisionResult(message);
        })
        .catch(err => {
            const errorBanner = document.getElementById('detail-error-banner');
            const errorText = document.getElementById('detail-error-text');
            if (errorBanner && errorText) {
                errorText.textContent = `Send failed: ${err.message}. You can retry approving and sending.`;
                errorBanner.classList.remove('hidden');
            }
            hooks.refreshFeedAndStats();
            alert(err.message);
        })
        .finally(() => {
            if (approveBtn) {
                approveBtn.disabled = false;
                approveBtn.classList.remove('loading');
            }
        });
}

/**
 * Submit a decline request for an event record.
 * @param {string} emailId Target email identifier.
 * @param {Record<string, any>} [payload={}] Decision payload.
 */
export function declineEvent(emailId, payload = {}) {
    const body = cleanDecisionPayload(payload);
    postJsonWithCsrf(`/api/events/${encodeURIComponent(emailId)}/decline`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body,
    })
        .then(async response => {
            const resPayload = await response.json();
            if (!response.ok) {
                throw new Error(resPayload.error || 'Decline failed');
            }
            return resPayload;
        })
        .then(() => {
            handleDecisionResult('Event declined. No invite will be sent.');
        })
        .catch(err => alert(err.message));
}

/**
 * Validate recipients and approve the currently opened modal event.
 */
export function approveCurrentEvent() {
    if (!currentDetail) return;
    const payload = getDecisionPayload();
    if (!payload.recipients || !payload.recipients.length) {
        alert('Select at least one recipient before sending.');
        return;
    }
    approveEvent(currentDetail.email_id, payload);
}

/**
 * Decline the currently opened modal event.
 */
export function declineCurrentEvent() {
    if (!currentDetail) return;
    const payload = getDecisionPayload();
    declineEvent(currentDetail.email_id, payload);
}

/**
 * Trigger a mailbox rescan for the currently open event record.
 */
export function rescanCurrentEmail() {
    if (!currentDetail || !currentDetail.email_id) return;
    const btn = document.getElementById('detail-rescan-btn');
    const doubleCheck = document.getElementById('rescan-double-check')?.checked || false;
    const hintVal = document.getElementById('rescan-hint')?.value?.trim() || undefined;
    if (btn) {
        btn.disabled = true;
        btn.classList.add('loading');
        btn.textContent = doubleCheck ? 'Auditing & Rescanning...' : 'Rescanning...';
    }
    postJsonWithCsrf(`/api/events/${encodeURIComponent(currentDetail.email_id)}/rescan`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ double_check: doubleCheck, hint: hintVal }),
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Rescan failed');
            }
            return payload;
        })
        .then(data => {
            alert(data.message || 'Email rescanned successfully');
            hooks.refreshFeedAndStats();
            if (data.record) {
                openEventDetails(data.record.email_id);
            } else {
                closeDetails();
            }
        })
        .catch(err => alert(`Rescan error: ${err.message}`))
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('loading');
                btn.textContent = '🔄 Rescan Email';
            }
        });
}

