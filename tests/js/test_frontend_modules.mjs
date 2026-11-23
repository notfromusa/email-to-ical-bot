import test from 'node:test';
import assert from 'node:assert/strict';

import {
    decodeIcsText,
    escapeHtml,
    formatBytes,
    parseIcsContent,
    parseIcsDateValue,
} from '../../static/js/ics.js';
import {
    formatDuration,
    getCalendarEvents,
    getEventStatusClass,
    getWeekEventStatusClass,
    isPending,
    isSameDay,
    layoutDayEvents,
    parseEventDate,
} from '../../static/js/calendar.js';

test('escapeHtml neutralizes HTML tags, quotes, and ampersands', () => {
    const raw = `<script>alert("xss" & '1')</script>`;
    assert.equal(
        escapeHtml(raw),
        '&lt;script&gt;alert(&quot;xss&quot; &amp; &#39;1&#39;)&lt;/script&gt;'
    );
    assert.equal(escapeHtml(null), '');
    assert.equal(escapeHtml(undefined), '');
});

test('decodeIcsText unescapes RFC 5545 escaped sequences', () => {
    assert.equal(
        decodeIcsText(String.raw`Room A\, Building 2\; Floor 3\nBring notes\\laptop`),
        'Room A, Building 2; Floor 3\nBring notes\\laptop'
    );
});

test('formatBytes formats zero and binary prefixes cleanly', () => {
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(512), '512 B');
    assert.equal(formatBytes(2048), '2.0 KB');
});

test('parseIcsDateValue handles UTC, local datetime, and DATE-only formats', () => {
    const utcDate = parseIcsDateValue('20261118T143000Z');
    assert.ok(utcDate instanceof Date);
    assert.equal(utcDate.toISOString(), '2026-11-18T14:30:00.000Z');

    const localDt = parseIcsDateValue('20261118T091500');
    assert.ok(localDt instanceof Date);
    assert.equal(localDt.getFullYear(), 2026);
    assert.equal(localDt.getMonth(), 10);
    assert.equal(localDt.getDate(), 18);
    assert.equal(localDt.getHours(), 9);
    assert.equal(localDt.getMinutes(), 15);

    const dateOnly = parseIcsDateValue('20261119');
    assert.ok(dateOnly instanceof Date);
    assert.equal(dateOnly.getFullYear(), 2026);
    assert.equal(dateOnly.getMonth(), 10);
    assert.equal(dateOnly.getDate(), 19);
});

test('parseIcsContent unfolds RFC 5545 folded lines and extracts VEVENT fields', () => {
    const ics = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'BEGIN:VEVENT',
        'SUMMARY:Architecture\\, Review &',
        '  Planning Sync',
        'DTSTART:20261118T140000Z',
        'DTEND:20261118T153000Z',
        'LOCATION:Room 101\\, Lab B',
        'DESCRIPTION:Line one\\nLine two',
        'ORGANIZER;CN=Alice Lead:mailto:alice@example.com',
        'ATTENDEE;CN=Bob Dev:mailto:bob@example.com',
        'END:VEVENT',
        'END:VCALENDAR',
    ].join('\r\n');

    const parsed = parseIcsContent(ics);
    assert.equal(parsed.summary, 'Architecture, Review & Planning Sync');
    assert.equal(parsed.location, 'Room 101, Lab B');
    assert.equal(parsed.description, 'Line one\nLine two');
    assert.equal(parsed.organizer, 'Alice Lead <alice@example.com>');
    assert.deepEqual(parsed.attendees, ['Bob Dev <bob@example.com>']);
    assert.ok(parsed.start.length > 0 && parsed.start !== '—');
    assert.ok(parsed.end.length > 0 && parsed.end !== '—');
});

test('parseEventDate, isSameDay, and formatDuration compute dates and elapsed times accurately', () => {
    const dateOnly = parseEventDate('2026-11-18');
    assert.ok(dateOnly instanceof Date);
    assert.equal(dateOnly.getHours(), 9);

    const start = parseEventDate('2026-11-18 14:00:00');
    const end45 = parseEventDate('2026-11-18T14:45:00');
    const end2h = parseEventDate('2026-11-18T16:00:00');
    const end90 = parseEventDate('2026-11-18T15:30:00');

    assert.equal(isSameDay(dateOnly, start), true);
    assert.equal(isSameDay(start, parseEventDate('2026-11-19T14:00:00')), false);

    assert.equal(formatDuration(start, end45), '45m');
    assert.equal(formatDuration(start, end2h), '2h');
    assert.equal(formatDuration(start, end90), '1h 30m');
    assert.equal(formatDuration(end45, start), '');
});

test('isPending and getEventStatusClass handle multi-candidate and error states', () => {
    assert.equal(isPending({ approval_required: false, approval_status: 'pending' }), false);
    assert.equal(
        isPending({ approval_required: true, sent: false, status: 'pending_approval', approval_status: 'pending' }),
        true
    );
    assert.equal(
        isPending({
            approval_required: true,
            sent: true,
            approval_status: 'approved',
            event_candidates: [{ title: 'A' }, { title: 'B' }],
            sent_event_indices: [0],
        }),
        true
    );
    assert.equal(
        isPending({
            approval_required: true,
            sent: true,
            approval_status: 'declined',
            event_candidates: [{ title: 'A' }, { title: 'B' }],
            sent_event_indices: [0],
        }),
        false
    );

    assert.equal(getEventStatusClass({ status: 'send_error', pending: true }), 'pill--warn');
    assert.equal(getWeekEventStatusClass({ pending: true, sent: false }), 'event--pending');
    assert.equal(getEventStatusClass({ pending: false, dry_run: true, sent: false }), 'pill--dryrun');
});

test('getCalendarEvents expands multi-candidate emails and sets fallback 1h duration', () => {
    const records = [
        {
            email_id: 'email-multi',
            email_sender: 'chair@example.com',
            email_subject: 'Two workshops',
            status: 'pending_approval',
            approval_required: true,
            approval_status: 'pending',
            sent: false,
            dry_run: false,
            event_candidates: [
                {
                    title: 'Workshop A',
                    start_datetime: '2026-11-18T10:00:00',
                    end_datetime: '',
                    location: 'Hall 1',
                },
                {
                    title: 'Workshop B',
                    start_datetime: '2026-11-18T15:00:00',
                    end_datetime: '2026-11-18T16:30:00',
                    location: 'Hall 2',
                },
            ],
        },
        {
            email_id: 'email-single',
            email_sender: 'boss@example.com',
            email_subject: 'Standup',
            has_event: true,
            event_title: 'Weekly Standup',
            event_start: '2026-11-19T09:00:00',
            event_end: '2026-11-19T09:30:00',
            status: 'invite_sent',
            sent: true,
        },
    ];

    const events = getCalendarEvents(records);
    assert.equal(events.length, 3);
    assert.equal(events[0].id, 'email-multi_cand_0');
    assert.equal(events[0].end.getTime() - events[0].start.getTime(), 3600 * 1000);
    assert.equal(events[0].pending, true);
    assert.equal(events[1].candidate_index, 1);
    assert.equal(events[1].candidate_count, 2);
    assert.equal(events[2].id, 'email-single_main');
    assert.equal(events[2].sent, true);
});

test('layoutDayEvents separates overlapping meetings into distinct columns and keeps non-overlapping full width', () => {
    const d = (hour, min = 0) => new Date(2026, 10, 18, hour, min, 0);
    const dayEvents = [
        { id: 'overlap-1', start: d(9, 0), end: d(10, 30) },
        { id: 'overlap-2', start: d(9, 30), end: d(11, 0) },
        { id: 'short-later', start: d(13, 0), end: d(13, 15) },
    ];

    const laidOut = layoutDayEvents(dayEvents, 60);
    assert.equal(laidOut.length, 3);

    const byId = Object.fromEntries(laidOut.map(item => [item.event.id, item]));
    assert.equal(byId['overlap-1'].totalCols, 2);
    assert.equal(byId['overlap-2'].totalCols, 2);
    assert.notEqual(byId['overlap-1'].col, byId['overlap-2'].col);

    assert.equal(byId['short-later'].totalCols, 1);
    assert.equal(byId['short-later'].col, 0);
    assert.equal(byId['short-later'].top, 13 * 60);
    assert.equal(byId['short-later'].height, 26); // minimum height floor for 15m event
});

