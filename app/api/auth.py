# app/api/auth.py

from flask import Blueprint, jsonify

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/login', methods=['POST'])
def login():
    # Placeholder for future authentication logic.
    return jsonify({'message': 'Login endpoint - to be implemented'})