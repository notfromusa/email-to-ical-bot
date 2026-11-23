/**
 * Calendar date parsing, Outlook-style overlap column layout, and Month/Week grid rendering.
 */

import { escapeHtml, parseIcsDateValue } from './ics.js';

/**
 * Return true when an event record still requires an operator approval decision.
 * @param {Record<string, any>} record Normalized event record.
 * @returns {boolean} True if pending approval.
 */
export function isPending(record) {
    if (!record || !record.approval_required) {
        return false;
    }
    const candidates = Array.isArray(record.event_candidates) ? record.event_candidates : [];
    const sentIndices = Array.isArray(record.sent_event_indices) ? record.sent_event_indices : [];
    if (candidates.length > 1 && sentIndices.length < candidates.length) {
        const status = (record.approval_status || '').toLowerCase();
        return status !== 'declined';
    }
    if (record.sent) {
        return false;
    }
    const finalStatuses = ['invite_sent', 'declined', 'dry_run_preview'];
    const recordStatus = (record.status || '').toLowerCase();
    if (recordStatus && finalStatuses.includes(recordStatus)) {
        return false;
    }
    const status = (record.approval_status || '').toLowerCase();
    return !status || status === 'pending' || status === 'send_error';
}

/**
 * Parse an ISO, date-only, space-separated, or compact ICS timestamp into a `Date`.
 * @param {string|Date|null|undefined} value Input date value.
 * @returns {Date|null} Parsed `Date` or `null`.
 */
export function parseEventDate(value) {
    if (!value) return null;
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    const s = String(value).trim();
    if (!s) return null;

    const dateOnlyMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (dateOnlyMatch) {
        return new Date(Number(dateOnlyMatch[1]), Number(dateOnlyMatch[2]) - 1, Number(dateOnlyMatch[3]), 9, 0, 0);
    }

    const isoNormalized = (s.includes(' ') && !s.includes('T')) ? s.replace(' ', 'T') : s;
    const d = new Date(isoNormalized);
    if (!isNaN(d.getTime())) return d;

    const ics = parseIcsDateValue(s);
    if (ics && !isNaN(ics.getTime())) return ics;
    return null;
}

/**
 * Compare two `Date` objects for calendar day equality (local time).
 * @param {Date|null} d1 First date.
 * @param {Date|null} d2 Second date.
 * @returns {boolean} True if same calendar year, month, and day.
 */
export function isSameDay(d1, d2) {
    if (!d1 || !d2) return false;
    return d1.getFullYear() === d2.getFullYear() &&
        d1.getMonth() === d2.getMonth() &&
        d1.getDate() === d2.getDate();
}

/**
 * Format 24-hour time (`HH:MM`) for compact calendar cells and cards.
 * @param {Date|null} date Input `Date`.
 * @returns {string} Formatted short time.
 */
export function formatShortTime(date) {
    if (!date) return '';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/**
 * Format compact month/day + time label for tooltips.
 * @param {Date|null} date Input `Date`.
 * @returns {string} Formatted short date/time label.
 */
export function formatShortDateTime(date) {
    if (!date) return '';
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + formatShortTime(date);
}

/**
 * Format elapsed duration between `start` and `end` (e.g. `'1h 30m'`, `'45m'`).
 * @param {Date|null} start Start time.
 * @param {Date|null} end End time.
 * @returns {string} Human-readable duration label.
 */
export function formatDuration(start, end) {
    if (!start || !end) return '';
    const diffMs = end.getTime() - start.getTime();
    if (diffMs <= 0) return '';
    const totalMinutes = Math.round(diffMs / (1000 * 60));
    const hours = Math.floor(totalMinutes / 60);
    const mins = totalMinutes % 60;
    if (hours > 0 && mins > 0) return `${hours}h ${mins}m`;
    if (hours > 0) return `${hours}h`;
    return `${mins}m`;
}

/**
 * Resolve CSS modifier class for month-grid event pills.
 * @param {Record<string, any>} ev Calendar event item.
 * @returns {string} CSS modifier class.
 */
export function getEventStatusClass(ev) {
    if (ev.status === 'llm_error' || ev.status === 'send_error') return 'pill--warn';
    if (ev.pending) return 'pill--pending';
    if (ev.sent) return 'pill--sent';
    if (ev.dry_run) return 'pill--dryrun';
    return 'pill--sent';
}

/**
 * Resolve CSS modifier class for week-grid proportional event blocks.
 * @param {Record<string, any>} ev Calendar event item.
 * @returns {string} CSS modifier class.
 */
export function getWeekEventStatusClass(ev) {
    if (ev.status === 'llm_error' || ev.status === 'send_error') return 'event--warn';
    if (ev.pending) return 'event--pending';
    if (ev.sent) return 'event--sent';
    if (ev.dry_run) return 'event--dryrun';
    return 'event--sent';
}

/**
 * Flatten processed email records and their event candidates into plottable calendar items.
 * @param {Array<Record<string, any>>} records Server `/api/events` records.
 * @returns {Array<Record<string, any>>} Normalized calendar items.
 */
export function getCalendarEvents(records) {
    const calendarEvents = [];
    if (!Array.isArray(records)) return calendarEvents;

    for (const record of records) {
        const candidates = Array.isArray(record.event_candidates) && record.event_candidates.length > 0
            ? record.event_candidates
            : null;

        if (candidates && candidates.length > 0) {
            candidates.forEach((cand, idx) => {
                const start = parseEventDate(cand.start_datetime);
                if (start) {
                    let end = parseEventDate(cand.end_datetime);
                    if (!end || end.getTime() <= start.getTime()) {
                        end = new Date(start.getTime() + 60 * 60 * 1000);
                    }
                    calendarEvents.push({
                        id: `${record.email_id || ''}_cand_${idx}`,
                        email_id: record.email_id,
                        title: cand.title || record.event_title || 'Untitled event',
                        start,
                        end,
                        location: cand.location || record.event_location || '',
                        description: cand.description || record.event_description || '',
                        status: record.status,
                        sent: Boolean(record.sent),
                        dry_run: Boolean(record.dry_run),
                        prompt_injection: Boolean(record.prompt_injection),
                        pending: isPending(record),
                        candidate_index: idx,
                        candidate_count: candidates.length,
                        email_sender: record.email_sender,
                        email_subject: record.email_subject,
                        record,
                    });
                }
            });
        } else if ((record.has_event || record.event_title) && record.event_start) {
            const start = parseEventDate(record.event_start);
            if (start) {
                let end = parseEventDate(record.event_end);
                if (!end || end.getTime() <= start.getTime()) {
                    end = new Date(start.getTime() + 60 * 60 * 1000);
                }
                calendarEvents.push({
                    id: `${record.email_id || ''}_main`,
                    email_id: record.email_id,
                    title: record.event_title || 'Untitled event',
                    start,
                    end,
                    location: record.event_location || '',
                    description: record.event_description || '',
                    status: record.status,
                    sent: Boolean(record.sent),
                    dry_run: Boolean(record.dry_run),
                    prompt_injection: Boolean(record.prompt_injection),
                    pending: isPending(record),
                    candidate_index: 0,
                    candidate_count: 1,
                    email_sender: record.email_sender,
                    email_subject: record.email_subject,
                    record,
                });
            }
        }
    }
    return calendarEvents;
}

/**
 * Compute proportional vertical positions and non-overlapping horizontal columns for one day's events.
 * @param {Array<Record<string, any>>} dayEvents Events scheduled on a single calendar day.
 * @param {number} hourHeight Pixel height representing one hour.
 * @returns {Array<Record<string, any>>} Layout descriptors (`top`, `height`, `col`, `totalCols`).
 */
export function layoutDayEvents(dayEvents, hourHeight) {
    if (!dayEvents || dayEvents.length === 0) return [];

    const items = dayEvents.map(e => {
        const startHours = e.start.getHours() + e.start.getMinutes() / 60;
        let endHours = e.end.getHours() + e.end.getMinutes() / 60;
        if (endHours <= startHours) {
            endHours = Math.min(24, startHours + 1);
        }
        const durationHours = Math.max(0.33, endHours - startHours);
        return {
            event: e,
            start: startHours,
            end: endHours,
            duration: durationHours,
            top: startHours * hourHeight,
            height: Math.max(26, durationHours * hourHeight),
            col: 0,
            totalCols: 1,
        };
    });

    items.sort((a, b) => a.start - b.start || b.duration - a.duration);

    const clusters = [];
    let currentCluster = [];
    let clusterEnd = -1;

    for (const item of items) {
        if (currentCluster.length === 0 || item.start < clusterEnd) {
            currentCluster.push(item);
            clusterEnd = Math.max(clusterEnd, item.end);
        } else {
            clusters.push(currentCluster);
            currentCluster = [item];
            clusterEnd = item.end;
        }
    }
    if (currentCluster.length > 0) {
        clusters.push(currentCluster);
    }

    for (const cluster of clusters) {
        const columns = [];
        for (const item of cluster) {
            let placed = false;
            for (let i = 0; i < columns.length; i++) {
                if (columns[i] <= item.start) {
                    item.col = i;
                    columns[i] = item.end;
                    placed = true;
                    break;
                }
            }
            if (!placed) {
                item.col = columns.length;
                columns.push(item.end);
            }
        }
        const totalCols = columns.length;
        for (const item of cluster) {
            item.totalCols = totalCols;
        }
    }

    return items;
}

/**
 * Apply dynamic vertical/horizontal coordinates to week-grid elements via CSSOM without inline `style=` HTML attributes.
 * @param {HTMLElement} container Week view container element.
 */
export function applyWeekCalendarGeometry(container) {
    if (!container) return;
    container.querySelectorAll('.cal-current-time-line[data-top]').forEach(el => {
        el.style.top = `${el.dataset.top}px`;
    });
    container.querySelectorAll('.cal-week-event-card[data-top]').forEach(el => {
        el.style.top = `${el.dataset.top}px`;
        el.style.height = `${el.dataset.height}px`;
        el.style.width = `calc(${el.dataset.widthPct}% - 4px)`;
        el.style.left = `calc(${el.dataset.leftPct}% + 2px)`;
    });
}

/**
 * Render the 7-column ISO Monday-first month calendar view.
 * @param {Object} params Render context.
 * @param {Date} params.calendarDate Active reference month date.
 * @param {Array<Record<string, any>>} params.records Event records to plot.
 * @param {string} params.currentFilter Active filter chip key.
 */
export function renderCalendarMonth({ calendarDate, records, currentFilter }) {
    const container = document.getElementById('calendar-month-view');
    if (!container) return;

    const year = calendarDate.getFullYear();
    const month = calendarDate.getMonth();

    const heading = document.getElementById('cal-current-label');
    if (heading) {
        heading.textContent = calendarDate.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
    }

    const events = getCalendarEvents(records);
    const firstDay = new Date(year, month, 1);
    const startDayOfWeek = (firstDay.getDay() + 6) % 7;
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const totalCells = Math.ceil((startDayOfWeek + daysInMonth) / 7) * 7;
    const startDate = new Date(year, month, 1 - startDayOfWeek);
    const today = new Date();

    const weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    let html = '<div class="calendar-month-grid">';

    for (const d of weekdays) {
        html += `<div class="cal-weekday-header">${d}</div>`;
    }

    let monthEventsCount = 0;

    for (let i = 0; i < totalCells; i++) {
        const cellDate = new Date(startDate.getFullYear(), startDate.getMonth(), startDate.getDate() + i);
        const isCurrentMonth = cellDate.getMonth() === month;
        const isTodayDate = isSameDay(cellDate, today);
        const cellEvents = events.filter(e => isSameDay(e.start, cellDate));
        if (isCurrentMonth) {
            monthEventsCount += cellEvents.length;
        }
        cellEvents.sort((a, b) => a.start - b.start);

        const classes = ['cal-month-cell'];
        if (!isCurrentMonth) classes.push('is-other-month');
        if (isTodayDate) classes.push('is-today');

        html += `<div class="${classes.join(' ')}" data-week-date-ms="${cellDate.getTime()}" title="Click to view week of ${escapeHtml(cellDate.toLocaleDateString())}">`;
        html += '  <div class="cal-cell-header">';
        html += `    <span class="cal-day-num">${cellDate.getDate()}</span>`;
        if (cellEvents.length > 0) {
            html += `    <span class="cal-day-count">${cellEvents.length}</span>`;
        }
        html += '  </div>';

        html += '  <div class="cal-cell-events">';
        const maxDisplay = 3;
        const displayed = cellEvents.slice(0, maxDisplay);
        for (const ev of displayed) {
            const timeStr = formatShortTime(ev.start);
            const statusClass = getEventStatusClass(ev);
            const durationStr = formatDuration(ev.start, ev.end);
            const tooltip = `${ev.title} (${durationStr})\n${formatShortDateTime(ev.start)} – ${formatShortDateTime(ev.end)}${ev.location ? '\n📍 ' + ev.location : ''}\nFrom: ${ev.email_sender || '—'}`;
            html += `    <div class="cal-event-pill ${statusClass}" title="${escapeHtml(tooltip)}" data-email-id="${escapeHtml(ev.email_id || '')}" data-pending="${ev.pending ? 'true' : 'false'}">`;
            html += `      <span class="pill-time">${escapeHtml(timeStr)}</span>`;
            html += `      <span class="pill-title">${escapeHtml(ev.title)}</span>`;
            html += '    </div>';
        }
        if (cellEvents.length > maxDisplay) {
            const overflow = cellEvents.length - maxDisplay;
            html += `    <div class="cal-more-badge" data-week-date-ms="${cellDate.getTime()}">+${overflow} more (week)</div>`;
        }
        html += '  </div>';
        html += '</div>';
    }

    html += '</div>';

    if (monthEventsCount === 0) {
        if (currentFilter === 'no_events') {
            html += '<div class="cal-empty-notice">No calendar events under "No events" filter. Switch to List view to inspect non-event emails.</div>';
        } else {
            html += '<div class="cal-empty-notice">No scheduled events detected in this month. Use &lt; Prev / Next &gt; to navigate or click "Scan Inbox Now".</div>';
        }
    }

    container.innerHTML = html;
}

/**
 * Render the Outlook-style 7-day proportional hourly schedule view.
 * @param {Object} params Render context.
 * @param {Date} params.calendarDate Date falling inside the target week.
 * @param {Array<Record<string, any>>} params.records Event records to plot.
 * @param {string} params.currentFilter Active filter chip key.
 */
export function renderCalendarWeek({ calendarDate, records, currentFilter }) {
    const container = document.getElementById('calendar-week-view');
    if (!container) return;

    const dayOfWeek = (calendarDate.getDay() + 6) % 7;
    const monday = new Date(
        calendarDate.getFullYear(),
        calendarDate.getMonth(),
        calendarDate.getDate() - dayOfWeek,
        0,
        0,
        0,
        0,
    );
    const weekDays = [];
    for (let i = 0; i < 7; i++) {
        weekDays.push(new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
    }

    const heading = document.getElementById('cal-current-label');
    if (heading) {
        const first = weekDays[0];
        const last = weekDays[6];
        const m1 = first.toLocaleDateString('en-US', { month: 'short' });
        const m2 = last.toLocaleDateString('en-US', { month: 'short' });
        const y1 = first.getFullYear();
        const y2 = last.getFullYear();
        if (y1 !== y2) {
            heading.textContent = `${m1} ${first.getDate()}, ${y1} – ${m2} ${last.getDate()}, ${y2}`;
        } else if (m1 !== m2) {
            heading.textContent = `${m1} ${first.getDate()} – ${m2} ${last.getDate()}, ${y1}`;
        } else {
            heading.textContent = `${m1} ${first.getDate()} – ${last.getDate()}, ${y1}`;
        }
    }

    const events = getCalendarEvents(records);
    const today = new Date();
    const weekdaysShort = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const HOUR_HEIGHT = 52;

    let html = '';

    html += '<div class="cal-week-header-row">';
    html += '  <div class="cal-week-header-time-spacer">Time</div>';
    for (let i = 0; i < 7; i++) {
        const dayDate = weekDays[i];
        const isTodayDate = isSameDay(dayDate, today);
        html += `  <div class="cal-week-day-col-header ${isTodayDate ? 'is-today' : ''}">`;
        html += `    <span class="cal-week-day-name">${weekdaysShort[i]}</span>`;
        html += `    <span class="cal-week-day-num">${dayDate.getDate()}</span>`;
        html += '  </div>';
    }
    html += '</div>';

    html += '<div class="cal-week-body-scroll" id="cal-week-scroll-container">';
    html += '  <div class="cal-week-time-grid">';

    html += '    <div class="cal-week-time-axis">';
    for (let h = 0; h < 24; h++) {
        const hourStr = `${String(h).padStart(2, '0')}:00`;
        html += `      <div class="cal-week-hour-label">${hourStr}</div>`;
    }
    html += '    </div>';

    let earliestEventHour = 8;
    let hasWeekEvents = false;

    for (let i = 0; i < 7; i++) {
        const dayDate = weekDays[i];
        const isTodayCol = isSameDay(dayDate, today);
        const dayEvents = events.filter(e => isSameDay(e.start, dayDate));

        if (dayEvents.length > 0) {
            hasWeekEvents = true;
            for (const ev of dayEvents) {
                const h = ev.start.getHours();
                if (h < earliestEventHour) {
                    earliestEventHour = h;
                }
            }
        }

        const layoutItems = layoutDayEvents(dayEvents, HOUR_HEIGHT);

        html += '    <div class="cal-week-day-column">';

        for (let h = 0; h < 24; h++) {
            html += '      <div class="cal-week-hour-slot"></div>';
        }

        if (isTodayCol) {
            const now = new Date();
            const nowTop = (now.getHours() + (now.getMinutes() / 60)) * HOUR_HEIGHT;
            html += `      <div class="cal-current-time-line" data-top="${nowTop}" title="Current time: ${formatShortTime(now)}"></div>`;
        }

        for (const item of layoutItems) {
            const ev = item.event;
            const statusClass = getWeekEventStatusClass(ev);
            const timeRange = `${formatShortTime(ev.start)} – ${formatShortTime(ev.end)}`;
            const durationText = formatDuration(ev.start, ev.end);
            const widthPct = 100 / item.totalCols;
            const leftPct = item.col * widthPct;
            const tooltip = `${ev.title} (${durationText})\n${timeRange}\n${ev.location ? '📍 ' + ev.location + '\n' : ''}From: ${ev.email_sender || '—'}`;

            html += `      <div class="cal-week-event-card ${statusClass}" `;
            html += `           data-top="${item.top}" data-height="${item.height}" data-width-pct="${widthPct}" data-left-pct="${leftPct}" `;
            html += `           title="${escapeHtml(tooltip)}" `;
            html += `           data-email-id="${escapeHtml(ev.email_id || '')}" data-pending="${ev.pending ? 'true' : 'false'}">`;
            html += '        <div class="cal-week-event-header">';
            html += `          <span class="cal-week-event-time">${escapeHtml(timeRange)}</span>`;
            if (durationText) {
                html += `          <span class="cal-week-event-duration">${escapeHtml(durationText)}</span>`;
            }
            html += '        </div>';
            html += `        <div class="cal-week-event-title">${escapeHtml(ev.title)}</div>`;
            if (ev.location && item.height >= 48) {
                html += `        <div class="cal-week-event-location">📍 ${escapeHtml(ev.location)}</div>`;
            }
            html += '      </div>';
        }

        html += '    </div>';
    }

    html += '  </div>';
    html += '</div>';

    if (!hasWeekEvents) {
        if (currentFilter === 'no_events') {
            html += '<div class="cal-empty-notice">No calendar events under "No events" filter. Switch to List view to inspect non-event emails.</div>';
        } else {
            html += '<div class="cal-empty-notice">No meetings scheduled for this week. Use &lt; Prev / Next &gt; to navigate weeks.</div>';
        }
    }

    container.innerHTML = html;
    applyWeekCalendarGeometry(container);

    setTimeout(() => {
        const scrollEl = document.getElementById('cal-week-scroll-container');
        if (scrollEl) {
            const targetHour = Math.max(0, earliestEventHour - 1);
            scrollEl.scrollTop = targetHour * HOUR_HEIGHT;
        }
    }, 10);
}

