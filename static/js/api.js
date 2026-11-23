/**
 * CSRF-aware HTTP helpers and session logout client utilities.
 */

/**
 * Read the active per-session CSRF token from the page meta tag.
 * @returns {string} Active CSRF token or empty string.
 */
export function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

/**
 * Update the page meta tag with a refreshed CSRF token.
 * @param {string} token New token string.
 */
export function setCsrfToken(token) {
    if (!token) return;
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
        meta.setAttribute('content', token);
    }
}

/**
 * Fetch the active CSRF token from `/api/csrf-token` and sync the page meta tag.
 * @returns {Promise<string>} Synchronized token string.
 */
export async function refreshCsrfToken() {
    try {
        const resp = await fetch('/api/csrf-token', { cache: 'no-store' });
        if (!resp.ok) return getCsrfToken();
        const data = await resp.json();
        if (data && data.csrf_token) {
            setCsrfToken(data.csrf_token);
            return data.csrf_token;
        }
    } catch (_) {
        // Keep current token on transient network failure.
    }
    return getCsrfToken();
}

/**
 * Send a state-changing HTTP request with `X-CSRF-Token`, retrying once after token sync on CSRF 403.
 * @param {string} url Target endpoint path.
 * @param {RequestInit} [options={}] Fetch options.
 * @returns {Promise<Response>} Fetch response.
 */
export async function postJsonWithCsrf(url, options = {}) {
    const buildInit = () => {
        const headers = Object.assign({}, options.headers || {}, {
            'X-CSRF-Token': getCsrfToken(),
        });
        return Object.assign({}, options, {
            method: options.method || 'POST',
            headers,
        });
    };
    let response = await fetch(url, buildInit());
    if (response.status === 403) {
        try {
            const errBody = await response.clone().json();
            if (errBody && typeof errBody.error === 'string' && errBody.error.toLowerCase().includes('csrf')) {
                await refreshCsrfToken();
                response = await fetch(url, buildInit());
            }
        } catch (_) {
            // Non-JSON 403 response; return original response.
        }
    }
    return response;
}

/**
 * End the current Web UI session and redirect to `/login`.
 */
export function logoutUser() {
    postJsonWithCsrf('/logout', {
        method: 'POST',
        headers: {
            Accept: 'application/json',
        },
    }).finally(() => {
        window.location.href = '/login';
    });
}

