/**
 * Main observatory application controller: state, view switching, polling, credentials, and DOM listeners.
 */

import { logoutUser, postJsonWithCsrf, refreshCsrfToken } from './api.js';
import {
    renderCalendarMonth,
    renderCalendarWeek,
} from './calendar.js';
import {
    addManualRecipient,
    approveCurrentEvent,
    closeDetails,
    declineCurrentEvent,
    getCurrentDetail,
    initEventsWorkflow,
    onRecipientCheckChanged,
    openEventDetails,
    populateEventEditor,
    removeManualRecipient,
    renderEventCard,
    rescanCurrentEmail,
    triggerIcsPreviewUpdate,
} from './events.js';
import { formatBytes, formatDate } from './ics.js';
import {
    closeManualEval,
    downloadManualEvalIcs,
    evaluateManualEmail,
    openManualEval,
    sendManualEvalIcsToSelf,
} from './manual-eval.js';

let currentFilter = 'all';
let dashboardConfig = {};
let censoring = false;
let purging = false;
let manualCheckPending = false;
let statsFailureCount = 0;
let credentialsConfigured = false;
let credentialMessageTimer = null;
let attachmentTogglePending = false;
let currentViewMode = localStorage.getItem('ical_dashboard_view_mode') || 'calendar';
let currentCalendarSubView = localStorage.getItem('ical_calendar_subview') || 'month';
let currentCalendarDate = new Date();
let cachedEvents = [];

/**
 * Sync visibility and active button states between Calendar and List modes.
 */
export function updateViewModeUI() {
    const isCal = currentViewMode === 'calendar';
    const calContainer = document.getElementById('calendar-view-container');
    const listContainer = document.getElementById('events-list');
    const calBtn = document.getElementById('view-mode-calendar');
    const listBtn = document.getElementById('view-mode-list');

    if (calContainer) {
        calContainer.classList.toggle('hidden', !isCal);
    }
    if (listContainer) {
        listContainer.classList.toggle('hidden', isCal);
    }
    if (calBtn) {
        calBtn.classList.toggle('active', isCal);
        calBtn.setAttribute('aria-selected', isCal ? 'true' : 'false');
    }
    if (listBtn) {
        listBtn.classList.toggle('active', !isCal);
        listBtn.setAttribute('aria-selected', !isCal ? 'true' : 'false');
    }
}

/**
 * Switch top-level feed view between `'calendar'` and `'list'`.
 * @param {'calendar'|'list'} mode Selected view mode.
 */
export function setViewMode(mode) {
    currentViewMode = mode;
    localStorage.setItem('ical_dashboard_view_mode', mode);
    updateViewModeUI();
    renderActiveView();
}

/**
 * Switch calendar sub-view between `'month'` and `'week'`.
 * @param {'month'|'week'} subView Selected sub-view.
 */
export function setCalendarSubView(subView) {
    currentCalendarSubView = subView;
    localStorage.setItem('ical_calendar_subview', subView);
    renderCalendarView();
}

/**
 * Step the calendar cursor backward or forward by one month or week.
 * @param {number} delta `-1` or `1`.
 */
export function navigateCalendar(delta) {
    if (currentCalendarSubView === 'month') {
        currentCalendarDate.setMonth(currentCalendarDate.getMonth() + delta);
    } else {
        currentCalendarDate.setDate(currentCalendarDate.getDate() + (delta * 7));
    }
    renderCalendarView();
}

/**
 * Reset the calendar cursor to today's date.
 */
export function navigateCalendarToday() {
    currentCalendarDate = new Date();
    renderCalendarView();
}

/**
 * Switch to Weekly view focused on the chosen date.
 * @param {Date|number|string} targetDate Target date.
 */
export function switchToWeekForDate(targetDate) {
    currentCalendarDate = new Date(targetDate);
    setCalendarSubView('week');
}

/**
 * Render whichever view mode (`calendar` or `list`) is currently active.
 */
export function renderActiveView() {
    updateViewModeUI();
    if (currentViewMode === 'calendar') {
        renderCalendarView();
    } else {
        renderListView();
    }
}

function renderListView() {
    const container = document.getElementById('events-list');
    if (!container) return;
    if (!cachedEvents.length) {
        container.innerHTML = '<div class="empty-state">No records yet for this filter. Trigger a new email to populate the feed.</div>';
        return;
    }
    container.innerHTML = cachedEvents.map(renderEventCard).join('');
}

function renderCalendarView() {
    const isMonth = currentCalendarSubView === 'month';
    const monthView = document.getElementById('calendar-month-view');
    const weekView = document.getElementById('calendar-week-view');
    const monthBtn = document.getElementById('subview-month-btn');
    const weekBtn = document.getElementById('subview-week-btn');

    if (monthView) monthView.classList.toggle('hidden', !isMonth);
    if (weekView) weekView.classList.toggle('hidden', isMonth);
    if (monthBtn) {
        monthBtn.classList.toggle('active', isMonth);
        monthBtn.setAttribute('aria-selected', isMonth ? 'true' : 'false');
    }
    if (weekBtn) {
        weekBtn.classList.toggle('active', !isMonth);
        weekBtn.setAttribute('aria-selected', !isMonth ? 'true' : 'false');
    }

    if (isMonth) {
        renderCalendarMonth({
            calendarDate: currentCalendarDate,
            records: cachedEvents,
            currentFilter,
        });
    } else {
        renderCalendarWeek({
            calendarDate: currentCalendarDate,
            records: cachedEvents,
            currentFilter,
        });
    }
}

function updateManualCheckUI(data) {
    const manualBtn = document.getElementById('manual-check-btn');
    const lastScan = document.getElementById('last-manual-check');
    const running = Boolean(data.manual_check_running);
    if (manualBtn) {
        const blocked = !credentialsConfigured;
        manualBtn.disabled = running || blocked;
        manualBtn.classList.toggle('loading', running);
        if (blocked) {
            manualBtn.textContent = 'Set mailbox password';
        } else {
            manualBtn.textContent = running ? 'Checking...' : 'Scan Inbox Now';
        }
        manualBtn.title = data.manual_check_error ? `Last error: ${data.manual_check_error}` : '';
    }
    if (lastScan) {
        lastScan.textContent = data.last_manual_check ? formatDate(data.last_manual_check) : '—';
    }
    if (!running) {
        manualCheckPending = false;
    }
}

function updateAttachmentToggleUI() {
    const btn = document.getElementById('attachment-toggle-btn');
    if (!btn) return;

    const enabled = dashboardConfig.attachment_parsing_enabled !== false;
    btn.disabled = attachmentTogglePending;
    btn.classList.toggle('loading', attachmentTogglePending);
    btn.classList.toggle('accent', enabled);
    btn.textContent = attachmentTogglePending
        ? 'Saving...'
        : `Attachments: ${enabled ? 'ON' : 'OFF'}`;
    btn.title = enabled
        ? 'Attachment text extraction is enabled'
        : 'Attachment text extraction is disabled';
}

function toggleAttachmentParsing() {
    if (attachmentTogglePending) {
        return;
    }

    const current = dashboardConfig.attachment_parsing_enabled !== false;
    attachmentTogglePending = true;
    updateAttachmentToggleUI();

    postJsonWithCsrf('/api/config/attachment-parsing', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ enabled: !current }),
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Failed to update setting');
            }
            return payload;
        })
        .then(payload => {
            dashboardConfig.attachment_parsing_enabled = Boolean(payload.attachment_parsing_enabled);
        })
        .catch(err => {
            alert(err.message);
        })
        .finally(() => {
            attachmentTogglePending = false;
            updateAttachmentToggleUI();
        });
}

function updateCredentialStatusUI() {
    const pill = document.getElementById('credential-status');
    const panel = document.getElementById('credentials-panel');
    const summary = document.getElementById('credential-summary');
    const formWrapper = document.getElementById('credentials-form-wrapper');
    if (!pill) return;
    pill.textContent = credentialsConfigured ? 'Status: configured' : 'Status: not set';
    pill.classList.toggle('active', credentialsConfigured);
    if (summary) {
        summary.classList.toggle('hidden', !credentialsConfigured);
    }
    if (formWrapper) {
        formWrapper.classList.toggle('hidden', credentialsConfigured);
    }
    if (panel) {
        panel.classList.toggle('hidden', credentialsConfigured);
    }
}

function toggleCredentialForm(show) {
    const summary = document.getElementById('credential-summary');
    const formWrapper = document.getElementById('credentials-form-wrapper');
    const panel = document.getElementById('credentials-panel');
    if (summary) {
        summary.classList.toggle('hidden', show);
    }
    if (formWrapper) {
        formWrapper.classList.toggle('hidden', !show);
    }
    if (panel) {
        panel.classList.toggle('hidden', !show);
    }
}

function loadCredentialStatus() {
    const previous = credentialsConfigured;
    fetch('/api/credentials/status')
        .then(r => r.json())
        .then(data => {
            credentialsConfigured = Boolean(data.configured);
            updateCredentialStatusUI();
            if (!previous && credentialsConfigured) {
                loadStats();
                loadEvents();
            }
        })
        .catch(() => {
            credentialsConfigured = false;
            updateCredentialStatusUI();
        });
}

function setCredentialMessage(message, isError = false) {
    const el = document.getElementById('credential-message');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('is-error', Boolean(isError));
    if (credentialMessageTimer) {
        clearTimeout(credentialMessageTimer);
    }
    if (message) {
        credentialMessageTimer = setTimeout(() => {
            el.textContent = '';
            el.classList.remove('is-error');
        }, 6000);
    }
}

function submitCredentials() {
    const passwordInput = document.getElementById('credential-password');
    const emailInput = document.getElementById('credential-email');
    const usernameInput = document.getElementById('credential-username');
    const password = passwordInput ? passwordInput.value.trim() : '';
    const emailAddress = emailInput ? emailInput.value.trim() : '';
    const exchangeUsername = usernameInput ? usernameInput.value.trim() : '';

    if (!password) {
        setCredentialMessage('Please enter the mailbox password.', true);
        return;
    }

    postJsonWithCsrf('/api/credentials', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            password,
            email_address: emailAddress,
            exchange_username: exchangeUsername,
        }),
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Failed to save credentials');
            }
            return payload;
        })
        .then(() => {
            if (passwordInput) {
                passwordInput.value = '';
            }
            setCredentialMessage('Credentials saved in memory.');
            credentialsConfigured = true;
            updateCredentialStatusUI();
            toggleCredentialForm(false);
            loadStats();
            loadEvents();
        })
        .catch(err => setCredentialMessage(err.message, true));
}

function setFilter(filter) {
    currentFilter = filter;
    document.querySelectorAll('.filter-chip').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    loadEvents();
}

function loadStats() {
    if (!credentialsConfigured) {
        updateCredentialStatusUI();
        updateManualCheckUI({ manual_check_running: false, last_manual_check: null, manual_check_error: null });
        return;
    }
    fetch('/api/stats')
        .then(r => r.json())
        .then(data => {
            statsFailureCount = 0;
            document.getElementById('processed-count').textContent = data.total_processed ?? data.db_entries ?? '-';
            document.getElementById('events-count').textContent = data.events_detected ?? '-';
            document.getElementById('sent-count').textContent = data.sent_events ?? '-';
            document.getElementById('dryrun-count').textContent = data.dry_run_events ?? '-';
            document.getElementById('detection-rate').textContent = data.detection_rate ? `${(data.detection_rate * 100).toFixed(1)}%` : '0%';
            const approvalMode = data.auto_approve_mode ? 'Auto-approve' : 'Manual approval';
            document.getElementById('mode-indicator').textContent = `Mode: ${data.dry_run_mode ? 'Dry run' : 'Live'} · ${approvalMode}`;
            document.getElementById('upcoming-count').textContent = data.upcoming_events ?? '-';
            document.getElementById('past-count').textContent = data.past_events ?? '-';
            document.getElementById('last-processed').textContent = data.last_processed_at ? formatDate(data.last_processed_at) : '—';
            document.getElementById('pending-count').textContent = data.pending_approvals ?? '-';
            document.getElementById('db-entries').textContent = data.db_entries ?? '-';
            document.getElementById('db-size').textContent = formatBytes(data.db_size_bytes ?? 0);
            updateManualCheckUI(data);
        })
        .catch(() => {
            statsFailureCount += 1;
            document.getElementById('mode-indicator').textContent = 'Mode: unreachable';
            if (statsFailureCount >= 3) {
                location.reload();
            }
        });
}

function loadEvents() {
    if (!credentialsConfigured) {
        const container = document.getElementById('events-list');
        if (container) {
            container.innerHTML = '<div class="empty-state">Mailbox credentials are required before loading history.</div>';
        }
        const calMonth = document.getElementById('calendar-month-view');
        if (calMonth) {
            calMonth.innerHTML = '<div class="empty-state">Mailbox credentials are required before loading calendar.</div>';
        }
        const calWeek = document.getElementById('calendar-week-view');
        if (calWeek) {
            calWeek.innerHTML = '<div class="empty-state">Mailbox credentials are required before loading calendar.</div>';
        }
        return;
    }
    fetch(`/api/events?filter=${currentFilter}`)
        .then(r => r.json())
        .then(events => {
            cachedEvents = Array.isArray(events) ? events : [];
            renderActiveView();
        })
        .catch(() => {
            const container = document.getElementById('events-list');
            if (container) {
                container.innerHTML = '<div class="empty-state">Failed to load events.</div>';
            }
            const calMonth = document.getElementById('calendar-month-view');
            if (calMonth) {
                calMonth.innerHTML = '<div class="empty-state">Failed to load calendar events.</div>';
            }
        });
}

function censorDatabase() {
    if (censoring) {
        return;
    }
    if (!confirm('This will redact stored prompts and drop cached email bodies. Continue?')) {
        return;
    }
    censoring = true;
    postJsonWithCsrf('/api/database/censor', {
        method: 'POST',
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Censoring failed');
            }
            return payload;
        })
        .then(result => {
            const updated = result.records_updated ?? 0;
            const prompts = result.prompts_redacted ?? 0;
            alert(`Censored ${updated} record${updated === 1 ? '' : 's'} · Redacted prompts: ${prompts}`);
            loadStats();
            loadEvents();
        })
        .catch(err => alert(err.message))
        .finally(() => {
            censoring = false;
        });
}

function purgeOldEntries() {
    if (purging) {
        return;
    }
    if (!confirm('Remove records without events older than ~1 week and events whose end date is older than ~1 week?')) {
        return;
    }
    purging = true;
    postJsonWithCsrf('/api/database/purge', {
        method: 'POST',
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Purge failed');
            }
            return payload;
        })
        .then(result => {
            const removed = result.records_deleted ?? 0;
            const staleEvents = result.removed_event ?? 0;
            const staleInfo = result.removed_no_event ?? 0;
            alert(`Deleted ${removed} record${removed === 1 ? '' : 's'} (events: ${staleEvents}, non-events: ${staleInfo})`);
            loadStats();
            loadEvents();
        })
        .catch(err => alert(err.message))
        .finally(() => {
            purging = false;
        });
}

function triggerManualCheck() {
    if (manualCheckPending) {
        return;
    }
    manualCheckPending = true;
    const manualBtn = document.getElementById('manual-check-btn');
    if (manualBtn) {
        manualBtn.classList.add('loading');
        manualBtn.disabled = true;
        manualBtn.textContent = 'Checking...';
    }
    postJsonWithCsrf('/api/check-now', {
        method: 'POST',
    })
        .then(async response => {
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || 'Manual check failed');
            }
            return payload;
        })
        .then(() => {
            alert('Manual scan started. The feed will refresh when results arrive.');
            setTimeout(() => {
                loadStats();
                loadEvents();
            }, 2000);
        })
        .catch(err => {
            alert(err.message);
            manualCheckPending = false;
            if (manualBtn) {
                manualBtn.classList.remove('loading');
                manualBtn.disabled = false;
                manualBtn.textContent = 'Scan Inbox Now';
            }
        });
}

function loadConfig() {
    fetch('/api/config')
        .then(r => r.json())
        .then(cfg => {
            dashboardConfig = cfg;
            updateAttachmentToggleUI();
        })
        .catch(() => {
            if (dashboardConfig.attachment_parsing_enabled === undefined) {
                dashboardConfig.attachment_parsing_enabled = true;
            }
            updateAttachmentToggleUI();
        });
}

function bindEvents() {
    const candidateSelectElement = document.getElementById('event-candidate-select');
    if (candidateSelectElement) {
        candidateSelectElement.addEventListener('change', () => {
            const detail = getCurrentDetail();
            if (!detail) return;
            const candidates = Array.isArray(detail.event_candidates) ? detail.event_candidates : [];
            const idx = Number(candidateSelectElement.value);
            const cand = candidates[idx] || null;
            const sentIndices = Array.isArray(detail.sent_event_indices) ? detail.sent_event_indices : [];
            const isSent = sentIndices.includes(idx) || Boolean(detail.sent);
            populateEventEditor(cand, true, isSent);
            const approveBtn = document.getElementById('detail-approve-btn');
            if (approveBtn) {
                approveBtn.textContent = isSent ? '🔄 Re-send Invite' : 'Approve & Send';
            }
            triggerIcsPreviewUpdate();
        });
    }

    ['edit-event-title', 'edit-event-start', 'edit-event-end', 'edit-event-location', 'edit-event-description'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', triggerIcsPreviewUpdate);
    });

    document.querySelectorAll('.filter-chip').forEach(btn => {
        btn.addEventListener('click', () => setFilter(btn.dataset.filter));
    });

    window.addEventListener('pageshow', e => {
        if (e.persisted) {
            refreshCsrfToken();
        }
    });

    document.getElementById('logout-btn')?.addEventListener('click', logoutUser);
    document.getElementById('credential-change-btn')?.addEventListener('click', () => toggleCredentialForm(true));
    document.getElementById('credentials-form')?.addEventListener('submit', e => {
        e.preventDefault();
        submitCredentials();
    });
    document.getElementById('view-mode-calendar')?.addEventListener('click', () => setViewMode('calendar'));
    document.getElementById('view-mode-list')?.addEventListener('click', () => setViewMode('list'));
    document.getElementById('manual-check-btn')?.addEventListener('click', triggerManualCheck);
    document.getElementById('manual-eval-open-btn')?.addEventListener('click', openManualEval);
    document.getElementById('attachment-toggle-btn')?.addEventListener('click', toggleAttachmentParsing);
    document.getElementById('censor-db-btn')?.addEventListener('click', censorDatabase);
    document.getElementById('purge-db-btn')?.addEventListener('click', purgeOldEntries);
    document.getElementById('cal-prev-btn')?.addEventListener('click', () => navigateCalendar(-1));
    document.getElementById('cal-today-btn')?.addEventListener('click', navigateCalendarToday);
    document.getElementById('cal-next-btn')?.addEventListener('click', () => navigateCalendar(1));
    document.getElementById('subview-month-btn')?.addEventListener('click', () => setCalendarSubView('month'));
    document.getElementById('subview-week-btn')?.addEventListener('click', () => setCalendarSubView('week'));
    document.getElementById('details-close-x')?.addEventListener('click', closeDetails);
    document.getElementById('details-close-btn')?.addEventListener('click', closeDetails);
    document.getElementById('detail-rescan-btn')?.addEventListener('click', rescanCurrentEmail);
    document.getElementById('detail-decline-btn')?.addEventListener('click', declineCurrentEvent);
    document.getElementById('detail-approve-btn')?.addEventListener('click', approveCurrentEvent);
    document.getElementById('manual-eval-close-x')?.addEventListener('click', closeManualEval);
    document.getElementById('manual-eval-close-btn')?.addEventListener('click', closeManualEval);
    document.getElementById('manual-eval-download-btn')?.addEventListener('click', downloadManualEvalIcs);
    document.getElementById('manual-eval-send-self-btn')?.addEventListener('click', sendManualEvalIcsToSelf);
    document.getElementById('manual-eval-run-btn')?.addEventListener('click', evaluateManualEmail);

    document.getElementById('events-list')?.addEventListener('click', e => {
        const btn = e.target.closest('button[data-action][data-email-id]');
        if (btn) {
            e.stopPropagation();
            openEventDetails(btn.dataset.emailId, btn.dataset.focus === 'true');
            return;
        }
        const card = e.target.closest('article.event-card[data-email-id]');
        if (card && card.dataset.emailId) {
            openEventDetails(card.dataset.emailId);
        }
    });

    document.getElementById('calendar-month-view')?.addEventListener('click', e => {
        const pill = e.target.closest('.cal-event-pill[data-email-id]');
        if (pill && pill.dataset.emailId) {
            e.stopPropagation();
            openEventDetails(pill.dataset.emailId, pill.dataset.pending === 'true');
            return;
        }
        const more = e.target.closest('.cal-more-badge[data-week-date-ms]');
        if (more) {
            e.stopPropagation();
            switchToWeekForDate(new Date(Number(more.dataset.weekDateMs)));
            return;
        }
        const cell = e.target.closest('.cal-month-cell[data-week-date-ms]');
        if (cell) {
            switchToWeekForDate(new Date(Number(cell.dataset.weekDateMs)));
        }
    });

    document.getElementById('calendar-week-view')?.addEventListener('click', e => {
        const card = e.target.closest('.cal-week-event-card[data-email-id]');
        if (card && card.dataset.emailId) {
            e.stopPropagation();
            openEventDetails(card.dataset.emailId, card.dataset.pending === 'true');
        }
    });

    const recipientPickerEl = document.getElementById('recipient-picker');
    if (recipientPickerEl) {
        recipientPickerEl.addEventListener('change', e => {
            if (e.target.classList.contains('recipient-checkbox')) {
                onRecipientCheckChanged();
            }
        });
        recipientPickerEl.addEventListener('click', e => {
            const removeBtn = e.target.closest('.recipient-remove-btn[data-recipient-addr]');
            if (removeBtn) {
                removeManualRecipient(removeBtn.dataset.recipientAddr, e);
                return;
            }
            if (e.target.closest('#add-recipient-btn')) {
                addManualRecipient();
            }
        });
        recipientPickerEl.addEventListener('keydown', e => {
            if (e.target.id === 'new-recipient-input' && e.key === 'Enter') {
                e.preventDefault();
                addManualRecipient();
            }
        });
    }

    const detailsModal = document.getElementById('details-modal');
    if (detailsModal) {
        detailsModal.addEventListener('click', e => {
            if (!e.target.closest('.modal__content')) {
                closeDetails();
            }
        });
    }

    const manualEvalModal = document.getElementById('manual-eval-modal');
    if (manualEvalModal) {
        manualEvalModal.addEventListener('click', e => {
            if (!e.target.closest('.modal__content')) {
                closeManualEval();
            }
        });
    }

    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            if (detailsModal && detailsModal.classList.contains('open')) {
                closeDetails();
            }
            if (manualEvalModal && manualEvalModal.classList.contains('open')) {
                closeManualEval();
            }
        }
    });
}

function bootstrap() {
    initEventsWorkflow({
        getConfig: () => dashboardConfig,
        refreshFeedAndStats: () => {
            loadStats();
            loadEvents();
        },
    });
    bindEvents();
    updateViewModeUI();
    refreshCsrfToken();
    loadConfig();
    loadCredentialStatus();
    if (credentialsConfigured) {
        loadStats();
        loadEvents();
    }
    setInterval(() => {
        loadCredentialStatus();
        if (credentialsConfigured) {
            loadStats();
            loadEvents();
        }
    }, 10000);
}

bootstrap();

