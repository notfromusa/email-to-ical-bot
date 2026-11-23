/**
 * Ephemeral manual email evaluation modal, ICS download, and send-to-self actions.
 */

import { postJsonWithCsrf } from './api.js';
import { escapeHtml, formatDate, renderIcsPreview } from './ics.js';

let manualEvalPending = false;
let manualEvalResult = null;
let manualEvalSendPending = false;

/**
 * Render extracted candidate summary cards for the manual evaluation modal.
 * @param {Array<Record<string, any>>} events Extracted event candidates.
 */
export function renderManualEventCandidates(events) {
    const container = document.getElementById('manual-eval-events');
    if (!container) {
        return;
    }
    const list = Array.isArray(events) ? events : [];
    if (!list.length) {
        container.innerHTML = '<div class="empty-state">No event candidates extracted.</div>';
        return;
    }

    container.innerHTML = list.map((event, index) => `
        <div class="detail-card">
            <strong>Candidate ${index + 1}: ${escapeHtml(event.title || 'Untitled event')}</strong>
            <p class="detail-hint">Start: ${escapeHtml(formatDate(event.start_datetime))}</p>
            <p class="detail-hint">End: ${escapeHtml(formatDate(event.end_datetime))}</p>
            <p class="detail-hint">Location: ${escapeHtml(event.location || '—')}</p>
            <p>${escapeHtml(event.description || 'No description provided.')}</p>
        </div>
    `).join('');
}

/**
 * Return true when the latest manual evaluation produced a non-empty ICS preview.
 * @returns {boolean}
 */
export function manualEvalHasIcs() {
    return Boolean(
        manualEvalResult &&
        typeof manualEvalResult.ics_content === 'string' &&
        manualEvalResult.ics_content.trim(),
    );
}

/**
 * Update enabled/loading button states for manual evaluation download and send-self buttons.
 */
export function updateManualEvalActions() {
    const hasIcs = manualEvalHasIcs();
    const downloadBtn = document.getElementById('manual-eval-download-btn');
    const sendBtn = document.getElementById('manual-eval-send-self-btn');

    if (downloadBtn) {
        downloadBtn.disabled = !hasIcs;
    }

    if (sendBtn) {
        sendBtn.disabled = !hasIcs || manualEvalSendPending;
        sendBtn.classList.toggle('loading', manualEvalSendPending);
        sendBtn.textContent = manualEvalSendPending ? 'Sending...' : 'Send to my inbox';
    }
}

/**
 * Download the generated manual evaluation `.ics` file locally.
 */
export function downloadManualEvalIcs() {
    if (!manualEvalHasIcs()) {
        return;
    }

    const icsText = manualEvalResult.ics_content.trim();
    const rawName = (manualEvalResult.selected_event && manualEvalResult.selected_event.title) ||
        manualEvalResult.subject ||
        'manual-eval';
    const safeBaseName = String(rawName)
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'manual-eval';

    const blob = new Blob([icsText], { type: 'text/calendar;charset=utf-8' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${safeBaseName}.ics`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);

    const status = document.getElementById('manual-eval-status');
    if (status) {
        status.textContent = 'ICS file downloaded.';
    }
}

/**
 * Dispatch the evaluated event directly to the operator's own mailbox via server-side ICS regeneration.
 */
export function sendManualEvalIcsToSelf() {
    if (manualEvalSendPending || !manualEvalHasIcs()) {
        return;
    }

    manualEvalSendPending = true;
    updateManualEvalActions();

    const status = document.getElementById('manual-eval-status');
    if (status) {
        status.textContent = 'Sending ICS to configured inbox...';
    }

    const fallbackEvent = Array.isArray(manualEvalResult?.events) && manualEvalResult.events.length
        ? manualEvalResult.events[0]
        : null;

    postJsonWithCsrf('/api/manual-evaluate/send-self', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            subject: manualEvalResult.subject || '(Manual evaluation)',
            ics_content: manualEvalResult.ics_content,
            selected_event: manualEvalResult.selected_event || fallbackEvent,
        }),
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Failed to send invite');
            }
            return payload;
        })
        .then(payload => {
            const recipients = Array.isArray(payload.send_result?.recipients)
                ? payload.send_result.recipients
                : [];
            const target = recipients.length ? recipients.join(', ') : 'configured inbox';
            const sendStatus = payload.send_result?.status;
            const message = sendStatus === 'dry_run_preview'
                ? `Dry run only: invite preview prepared for ${target}.`
                : `Invite sent to ${target}.`;
            if (status) {
                status.textContent = message;
            }
            alert(message);
        })
        .catch(err => {
            const message = `Send failed: ${err.message}`;
            if (status) {
                status.textContent = message;
            }
            alert(message);
        })
        .finally(() => {
            manualEvalSendPending = false;
            updateManualEvalActions();
        });
}

/**
 * Clear previous manual evaluation outputs inside the modal.
 */
export function resetManualEvalOutput() {
    manualEvalResult = null;
    manualEvalSendPending = false;
    const status = document.getElementById('manual-eval-status');
    if (status) {
        status.textContent = 'No evaluation run yet.';
    }
    renderManualEventCandidates([]);
    renderIcsPreview('manual-eval-ics-summary', 'manual-eval-ics', '', 'No ICS preview generated yet.');
    const promptPre = document.getElementById('manual-eval-llm-prompt');
    const responsePre = document.getElementById('manual-eval-llm-response');
    if (promptPre) {
        promptPre.textContent = '(prompt unavailable)';
    }
    if (responsePre) {
        responsePre.textContent = '(response unavailable)';
    }
    updateManualEvalActions();
}

/**
 * Open the manual email evaluation modal dialog.
 */
export function openManualEval() {
    resetManualEvalOutput();
    const modal = document.getElementById('manual-eval-modal');
    if (modal) {
        modal.classList.add('open');
    }
    const bodyInput = document.getElementById('manual-eval-body');
    if (bodyInput && !bodyInput.value.trim()) {
        bodyInput.focus();
    }
}

/**
 * Close the manual email evaluation modal dialog.
 */
export function closeManualEval() {
    const modal = document.getElementById('manual-eval-modal');
    if (modal) {
        modal.classList.remove('open');
    }
}

/**
 * Submit pasted email text to `/api/manual-evaluate` and render extraction results.
 */
export function evaluateManualEmail() {
    if (manualEvalPending) {
        return;
    }
    const subject = (document.getElementById('manual-eval-subject')?.value || '').trim();
    const referenceTimestamp = (document.getElementById('manual-eval-reference')?.value || '').trim();
    const body = (document.getElementById('manual-eval-body')?.value || '').trim();
    if (!body) {
        alert('Paste email text before evaluating.');
        return;
    }

    const runBtn = document.getElementById('manual-eval-run-btn');
    manualEvalPending = true;
    manualEvalResult = null;
    manualEvalSendPending = false;
    updateManualEvalActions();
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.classList.add('loading');
        runBtn.textContent = 'Evaluating...';
    }

    postJsonWithCsrf('/api/manual-evaluate', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            subject,
            body,
            reference_timestamp: referenceTimestamp,
        }),
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Manual evaluation failed');
            }
            return payload;
        })
        .then(payload => {
            manualEvalResult = payload;
            updateManualEvalActions();

            const events = Array.isArray(payload.events) ? payload.events : [];
            const statusParts = [];
            if (payload.has_event) {
                statusParts.push(`Detected ${events.length} event candidate(s).`);
            } else {
                statusParts.push('No event detected.');
            }
            if (payload.prompt_injection) {
                statusParts.push('Prompt-injection markers detected in pasted text.');
            }
            if (payload.ics_error) {
                statusParts.push(`ICS preview: ${payload.ics_error}`);
            }

            const status = document.getElementById('manual-eval-status');
            if (status) {
                status.textContent = statusParts.join(' ');
            }

            renderManualEventCandidates(events);
            renderIcsPreview(
                'manual-eval-ics-summary',
                'manual-eval-ics',
                payload.ics_content || '',
                'No ICS preview generated for this input.',
            );

            document.getElementById('manual-eval-llm-prompt').textContent = payload.llm_prompt || '(prompt unavailable)';
            const llmResponse = typeof payload.llm_raw_result === 'object'
                ? JSON.stringify(payload.llm_raw_result, null, 2)
                : (payload.llm_response || '');
            document.getElementById('manual-eval-llm-response').textContent = llmResponse || '(response unavailable)';
        })
        .catch(err => {
            manualEvalResult = null;
            updateManualEvalActions();

            const status = document.getElementById('manual-eval-status');
            if (status) {
                status.textContent = `Evaluation failed: ${err.message}`;
            }
            renderManualEventCandidates([]);
            renderIcsPreview('manual-eval-ics-summary', 'manual-eval-ics', '', 'No ICS preview generated.');
        })
        .finally(() => {
            manualEvalPending = false;
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.classList.remove('loading');
                runBtn.textContent = 'Evaluate';
            }
        });
}

