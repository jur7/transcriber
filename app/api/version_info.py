# app/api/version_info.py

import logging
from flask import Blueprint, jsonify
from app.models.transcription import get_db
from app.version import __version__ as CODE_VERSION, __build__ as CODE_BUILD


version_bp = Blueprint('version_bp', __name__)


@version_bp.route('/version', methods=['GET'])
def get_version_info():
    """Returns app version/build info, preferring DB values and falling back to code.
    Response example:
    {
      "version": "0.1.0",
      "build": "202501010930",
      "version_full": "0.1.0+202501010930",
      "source": "db" | "code" | "mixed"
    }
    """
    try:
        db = get_db()
        rows = db.execute(
            """
            SELECT key, value FROM app_meta
            WHERE key IN ('app_version','app_build')
            """
        ).fetchall()
        meta = {row['key']: row['value'] for row in rows}

        # Prefer DB values if present, otherwise fall back to code
        version = meta.get('app_version') or CODE_VERSION
        build_db = meta.get('app_build')
        build_code = CODE_BUILD or ''
        build = build_db if (build_db and build_db.strip()) else build_code

        # Construct version_full dynamically
        version_full = f"{version}+{build}" if build else version

        # Determine source attribution
        version_from_db = 'app_version' in meta
        build_from_db = bool(build_db and build_db.strip())
        if version_from_db and build_from_db:
            source = 'db'
        elif (version_from_db and not build_from_db) or (not version_from_db and build_from_db):
            source = 'mixed'
        else:
            source = 'code'

        return jsonify({
            'version': version,
            'build': build,
            'version_full': version_full,
            'source': source,
        })
    except Exception as e:
        logging.debug(f"[API:/version] Falling back to code version: {e}")
        build = CODE_BUILD or ''
        return jsonify({
            'version': CODE_VERSION,
            'build': build,
            'version_full': f"{CODE_VERSION}+{build}" if build else CODE_VERSION,
            'source': 'code',
        })
