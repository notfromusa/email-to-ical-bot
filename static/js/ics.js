/**
 * Pure formatting, HTML escaping, and RFC 5545 ICS parsing/preview utilities.
 */

/**
 * Format an ISO/date string using locale formatting.
 * @param {string|null|undefined} value Date string.
 * @returns {string} Formatted timestamp or `'—'`.
 */
export function formatDate(value) {
    if (!value) return '—';
    return new Date(value).toLocaleString();
}

/**
 * Format a byte count into human-readable units.
 * @param {number} bytes Byte count.
 * @returns {string} Formatted byte size string.
 */
export function formatBytes(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const value = bytes / Math.pow(1024, exponent);
    return `${value.toFixed(value > 10 ? 0 : 1)} ${units[exponent]}`;
}

/**
 * HTML-escape untrusted strings before interpolation into innerHTML templates.
 * @param {unknown} value Untrusted value.
 * @returns {string} Escaped string safe for HTML element bodies and quoted attributes.
 */
export function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Unescape RFC 5545 Section 3.3.11 escaped text sequences.
 * @param {string} value Raw ICS property value.
 * @returns {string} Unescaped plain string.
 */
export function decodeIcsText(value) {
    if (!value) return '';
    return String(value)
        .replace(/\\n/gi, '\n')
        .replace(/\\,/g, ',')
        .replace(/\\;/g, ';')
        .replace(/\\\\/g, '\\');
}

/**
 * Parse an iCalendar `DATE` or `DATE-TIME` string into a JavaScript `Date`.
 * @param {string} value Raw ICS datetime string.
 * @returns {Date|null} Parsed `Date`, or `null` if invalid.
 */
export function parseIcsDateValue(value) {
    if (!value) return null;
    const text = String(value).trim();
    let match = text.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/);
    if (match) {
        const year = Number(match[1]);
        const month = Number(match[2]) - 1;
        const day = Number(match[3]);
        const hour = Number(match[4]);
        const minute = Number(match[5]);
        const second = Number(match[6]);
        if (match[7] === 'Z') {
            return new Date(Date.UTC(year, month, day, hour, minute, second));
        }
        return new Date(year, month, day, hour, minute, second);
    }

    match = text.match(/^(\d{4})(\d{2})(\d{2})$/);
    if (match) {
        return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    }

    const fallback = new Date(text);
    return Number.isNaN(fallback.getTime()) ? null : fallback;
}

/**
 * Parse and locale-format an iCalendar datetime property value.
 * @param {string} value Raw ICS datetime value.
 * @returns {string} Formatted date/time string.
 */
export function formatIcsDateValue(value) {
    const parsed = parseIcsDateValue(value);
    if (!parsed) {
        return value ? String(value) : '—';
    }
    return parsed.toLocaleString();
}

/**
 * Unfold and parse core VEVENT properties from an RFC 5545 `.ics` payload.
 * @param {string} icsText Raw `.ics` text.
 * @returns {{summary: string, start: string, end: string, location: string, description: string, organizer: string, attendees: string[]}}
 */
export function parseIcsContent(icsText) {
    const result = {
        summary: '',
        start: '',
        end: '',
        location: '',
        description: '',
        organizer: '',
        attendees: [],
    };

    if (!icsText) {
        return result;
    }

    const rawLines = String(icsText).split(/\r?\n/);
    const unfolded = [];
    for (const rawLine of rawLines) {
        const line = rawLine || '';
        if ((line.startsWith(' ') || line.startsWith('\t')) && unfolded.length) {
            unfolded[unfolded.length - 1] += line.slice(1);
        } else if (line.trim().length > 0) {
            unfolded.push(line.trim());
        }
    }

    for (const line of unfolded) {
        if (!line || !line.includes(':')) {
            continue;
        }

        const separator = line.indexOf(':');
        const left = line.slice(0, separator);
        const rawValue = line.slice(separator + 1);
        const key = left.split(';')[0].toUpperCase();
        const value = decodeIcsText(rawValue);

        if (key === 'SUMMARY') {
            result.summary = value;
        } else if (key === 'DTSTART') {
            result.start = formatIcsDateValue(rawValue);
        } else if (key === 'DTEND') {
            result.end = formatIcsDateValue(rawValue);
        } else if (key === 'LOCATION') {
            result.location = value;
        } else if (key === 'DESCRIPTION') {
            result.description = value;
        } else if (key === 'ORGANIZER') {
            const cnMatch = left.match(/CN=([^;:]+)/i);
            const email = rawValue.replace(/^mailto:/i, '');
            result.organizer = cnMatch ? `${decodeIcsText(cnMatch[1])} <${email}>` : email;
        } else if (key === 'ATTENDEE') {
            const cnMatch = left.match(/CN=([^;:]+)/i);
            const email = rawValue.replace(/^mailto:/i, '');
            result.attendees.push(cnMatch ? `${decodeIcsText(cnMatch[1])} <${email}>` : email);
        }
    }

    return result;
}

/**
 * Render parsed ICS summary cards and raw ICS text into DOM containers.
 * @param {string|null} summaryContainerId Target summary grid element ID.
 * @param {string|null} rawContainerId Target raw `<pre>` element ID.
 * @param {string} icsText Raw `.ics` payload.
 * @param {string} [emptyMessage='No ICS preview available.'] Fallback empty notice.
 */
export function renderIcsPreview(summaryContainerId, rawContainerId, icsText, emptyMessage = 'No ICS preview available.') {
    if (typeof document === 'undefined') return;
    const summaryContainer = summaryContainerId ? document.getElementById(summaryContainerId) : null;
    const rawContainer = rawContainerId ? document.getElementById(rawContainerId) : null;

    const rawText = (icsText || '').trim();
    if (rawContainer) {
        rawContainer.textContent = rawText || '(no ICS content)';
    }

    if (!summaryContainer) {
        return;
    }

    if (!rawText) {
        summaryContainer.innerHTML = `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
        return;
    }

    const parsed = parseIcsContent(rawText);
    const cards = [
        { label: 'Title', value: parsed.summary || '—' },
        { label: 'Start', value: parsed.start || '—' },
        { label: 'End', value: parsed.end || '—' },
        { label: 'Location', value: parsed.location || '—' },
        { label: 'Organizer', value: parsed.organizer || '—' },
        { label: 'Attendees', value: parsed.attendees.length ? parsed.attendees.join('\n') : '—' },
        { label: 'Description', value: parsed.description || '—' },
    ];

    summaryContainer.innerHTML = cards.map(card => `
        <div class="ics-preview-card">
            <strong>${escapeHtml(card.label)}</strong>
            <p>${escapeHtml(card.value)}</p>
        </div>
    `).join('');
}

