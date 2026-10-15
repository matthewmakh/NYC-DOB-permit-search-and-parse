"""Authenticated, team-scoped manual owner research endpoints."""
import os
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from auth_service import login_required
from crm_service import crm_context
import owner_research


owner_research_bp = Blueprint('owner_research', __name__)


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def request_origin():
    """Use the public scheme only behind the configured Railway/trusted proxy.

    The host always comes from Flask's request host, never X-Forwarded-Host.
    Outside Railway, trusting the proxy protocol requires explicit app config.
    """
    scheme = request.scheme
    trusted_proxy = bool(os.getenv('RAILWAY_ENVIRONMENT_ID')) or current_app.config.get('OWNER_RESEARCH_TRUST_PROXY_PROTO', False)
    forwarded = request.headers.get('X-Forwarded-Proto', '').lower()
    if trusted_proxy and forwarded in ('http', 'https'):
        scheme = forwarded
    return _origin(f'{scheme}://{request.host}')


def same_origin_review_request():
    # A custom header + JSON prevents simple cross-origin form submissions.
    # Explicit origin checks below also reject requests under permissive CORS.
    if not request.is_json or request.headers.get('X-Owner-Research') != '1':
        return False
    if request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
        return False
    try:
        for header in ('Origin', 'Referer'):
            value = request.headers.get(header)
            if value and _origin(value) != request_origin():
                return False
    except ValueError:
        return False
    return True


@owner_research_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie'
    return response


@owner_research_bp.route('/api/property/<bbl>/owner-research', methods=['GET'])
@login_required
def get_research(bbl):
    try:
        return jsonify(owner_research.get_owner_research(bbl, crm_context(g.user)))
    except owner_research.ResearchError as exc:
        return jsonify(success=False, error=str(exc)), exc.status_code
    except Exception:
        current_app.logger.exception('Owner research read failed')
        return jsonify(success=False, error='Owner research is temporarily unavailable'), 503


@owner_research_bp.route('/api/property/<bbl>/owner-research/<person_id>', methods=['POST'])
@login_required
def save_research(bbl, person_id):
    if not same_origin_review_request():
        return jsonify(success=False, error='A same-origin JSON request is required'), 403
    if request.content_length and request.content_length > 16384:
        return jsonify(success=False, error='Review is too large'), 413
    try:
        return jsonify(owner_research.save_owner_research(bbl, person_id, request.get_json(silent=True), crm_context(g.user)))
    except owner_research.ResearchError as exc:
        return jsonify(success=False, error=str(exc)), exc.status_code
    except Exception:
        current_app.logger.exception('Owner research save failed')
        return jsonify(success=False, error='Unable to save the review right now'), 503
