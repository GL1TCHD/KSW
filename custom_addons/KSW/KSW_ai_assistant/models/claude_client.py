"""Minimal Claude Messages API client over HTTP.

Raw HTTP on purpose: the official ``anthropic`` package is not in the
Odoo image, and project policy is not to add Python packages. ``requests``
already ships with Odoo.
"""
import logging

import requests

_logger = logging.getLogger(__name__)

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-opus-5'
# A declined request is re-run server-side on Anthropic's recommended
# fallback model instead of coming back as a refusal.
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
TIMEOUT = 120  # seconds


class ClaudeError(Exception):
    """A failure the user should see as a plain message."""


def create_message(api_key, model, system, messages, tools,
                   effort='medium', max_tokens=16000):
    headers = {
        'x-api-key': api_key,
        'anthropic-version': API_VERSION,
        'anthropic-beta': FALLBACK_BETA,
        'content-type': 'application/json',
    }
    body = {
        'model': model or DEFAULT_MODEL,
        'max_tokens': max_tokens,
        'system': system,
        'messages': messages,
        'tools': tools,
        'output_config': {'effort': effort},
        'fallbacks': 'default',
        # Caches the tools + system prompt across turns and users.
        'cache_control': {'type': 'ephemeral'},
    }
    try:
        resp = requests.post(API_URL, json=body, headers=headers,
                             timeout=TIMEOUT)
    except requests.RequestException as exc:
        _logger.warning('Claude API unreachable: %s', exc)
        raise ClaudeError('connection') from exc
    if resp.status_code == 401:
        raise ClaudeError('auth')
    if resp.status_code == 429:
        raise ClaudeError('rate_limit')
    if resp.status_code >= 400:
        _logger.warning('Claude API error %s: %s', resp.status_code,
                        resp.text[:2000])
        raise ClaudeError('api')
    return resp.json()
