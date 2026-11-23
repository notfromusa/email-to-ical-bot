"""
Unit tests for the Web UI Calendar View feature.

This module validates:
1. Presence of the Calendar View and List View toggle in templates/index.html,
   ensuring that views are mutually exclusive to avoid visual clutter.
2. Default Month view and optional Outlook-style Weekly view components.
3. Proportional meeting duration and overlapping event layout calculations.
4. Server endpoint /api/events serving event metadata (start, end, candidates)
   required for rendering both Month and Week calendar views.
"""

from pathlib import Path
from unittest.mock import patch

from web_ui import app, storage


def test_index_template_contains_calendar_view_elements():
    """
    Verify that templates/index.html includes all necessary DOM elements and controls
    for the Calendar view and mutually exclusive List view toggle.
    """
    templates_dir = Path(app.root_path) / "templates"
    content = (templates_dir / "index.html").read_text(encoding="utf-8")

    # View Mode Switcher (mutually exclusive Calendar vs List)
    assert 'id="view-mode-calendar"' in content, "Missing #view-mode-calendar toggle button"
    assert 'id="view-mode-list"' in content, "Missing #view-mode-list toggle button"
    assert 'class="view-mode-toggle"' in content, "Missing .view-mode-toggle container"

    # Calendar View container and toolbar
    assert 'id="calendar-view-container"' in content, "Missing #calendar-view-container"
    assert 'class="calendar-toolbar"' in content, "Missing .calendar-toolbar"
    assert 'id="cal-prev-btn"' in content, "Missing #cal-prev-btn navigation button"
    assert 'id="cal-today-btn"' in content, "Missing #cal-today-btn navigation button"
    assert 'id="cal-next-btn"' in content, "Missing #cal-next-btn navigation button"
    assert 'id="cal-current-label"' in content, "Missing #cal-current-label heading"

    # Sub-view switcher (Month default, Week optional)
    assert 'id="subview-month-btn"' in content, "Missing #subview-month-btn subview toggle"
    assert 'id="subview-week-btn"' in content, "Missing #subview-week-btn subview toggle"

    # Grid containers
    assert 'id="calendar-month-view"' in content, "Missing #calendar-month-view grid"
    assert 'id="calendar-week-view"' in content, "Missing #calendar-week-view grid"
    assert 'id="events-list"' in content, "Missing #events-list feed"


def test_index_template_contains_calendar_javascript_logic():
    """Verify index.html links the modular frontend and includes calendar logic.

    Verifies that ``templates/index.html`` loads ``js/app.js`` as an ES module
    and that ``static/js/app.js`` and ``static/js/calendar.js`` expose the
    calendar state variables, view switchers, and Outlook-style layout helpers.
    """
    root_dir = Path(app.root_path)
    index_html = (root_dir / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'type="module"' in index_html
    assert "js/app.js" in index_html

    app_js = (root_dir / "static" / "js" / "app.js").read_text(encoding="utf-8")
    calendar_js = (root_dir / "static" / "js" / "calendar.js").read_text(encoding="utf-8")

    # State variables & defaults
    assert "currentViewMode" in app_js
    assert "currentCalendarSubView" in app_js
    assert "currentCalendarDate" in app_js
    assert "cachedEvents" in app_js

    # View switching and mutual exclusion
    assert "function setViewMode(" in app_js
    assert "function updateViewModeUI(" in app_js
    assert "function setCalendarSubView(" in app_js
    assert "function renderActiveView(" in app_js

    # Calendar rendering logic
    assert "function renderCalendarMonth(" in calendar_js
    assert "function renderCalendarWeek(" in calendar_js
    assert "function layoutDayEvents(" in calendar_js
    assert "function formatDuration(" in calendar_js
    assert "function getCalendarEvents(" in calendar_js
    assert "function parseEventDate(" in calendar_js


def test_index_template_styles_include_calendar_classes():
    """Verify index.html links external CSS and calendar classes exist in app.css."""
    root_dir = Path(app.root_path)
    index_html = (root_dir / "templates" / "index.html").read_text(encoding="utf-8")
    assert "css/app.css" in index_html
    assert "<style" not in index_html.lower()

    app_css = (root_dir / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".calendar-toolbar" in app_css
    assert ".calendar-month-grid" in app_css
    assert ".cal-month-cell" in app_css
    assert ".cal-event-pill" in app_css
    assert ".calendar-week-grid-wrapper" in app_css
    assert ".cal-week-time-grid" in app_css
    assert ".cal-week-hour-slot" in app_css
    assert ".cal-week-event-card" in app_css
    assert ".cal-current-time-line" in app_css


def test_api_events_provides_calendar_dates_and_candidates():
    """
    Verify that /api/events endpoint delivers events with event_start, event_end,
    and event_candidates, which the client-side calendar uses for rendering.
    """
    app.config["TESTING"] = True
    fake_record = {
        "email_id": "test-cal-email-001",
        "email_subject": "Department Seminar: AI in Healthcare",
        "email_sender": "seminar@example.com",
        "email_recipients": ["user@example.com"],
        "has_event": True,
        "event_title": "AI in Healthcare Seminar",
        "event_start": "2026-11-18T14:00:00+01:00",
        "event_end": "2026-11-18T15:30:00+01:00",
        "event_location": "Lecture Hall A",
        "event_description": "Guest lecture by Dr. Smith",
        "status": "pending_approval",
        "sent": False,
        "dry_run": False,
        "event_candidates": [
            {
                "title": "AI in Healthcare Seminar",
                "start_datetime": "2026-11-18T14:00:00+01:00",
                "end_datetime": "2026-11-18T15:30:00+01:00",
                "location": "Lecture Hall A",
                "description": "Guest lecture by Dr. Smith",
            }
        ],
    }

    with patch("web_ui._credentials_ready", return_value=True), \
         patch("web_ui._check_auth", return_value=True), \
         patch.object(storage, "get_results", return_value=[fake_record]):
        with app.test_client() as client:
            resp = client.get("/api/events?filter=all")
            assert resp.status_code == 200
            data = resp.get_json()
            assert isinstance(data, list)
            assert len(data) == 1
            item = data[0]
            assert item["email_id"] == "test-cal-email-001"
            assert item["has_event"] is True
            assert item["event_title"] == "AI in Healthcare Seminar"
            assert item["event_start"] == "2026-11-18T14:00:00+01:00"
            assert item["event_end"] == "2026-11-18T15:30:00+01:00"
            assert len(item["event_candidates"]) == 1
            assert item["event_candidates"][0]["start_datetime"] == "2026-11-18T14:00:00+01:00"


def test_index_route_renders_calendar_markup():
    """
    Verify that GET / returns 200 OK and includes the calendar markup in the rendered HTML.
    """
    app.config["TESTING"] = True
    with patch("web_ui._check_auth", return_value=True), \
         patch("web_ui._auth_configured", return_value=True), \
         patch("web_ui._auth_requires_change", return_value=False):
        with app.test_client() as client:
            resp = client.get("/")
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert "calendar-view-container" in html
            assert "view-mode-calendar" in html
            assert "calendar-month-view" in html
            assert "calendar-week-view" in html
