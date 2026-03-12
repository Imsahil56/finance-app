"""
routes/auth.py
Authentication blueprint — register, login, logout.
"""

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, session)
from werkzeug.security import generate_password_hash, check_password_hash

from routes.db import get_db

auth_bp = Blueprint('auth_bp', __name__)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('dashboard_bp.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')
        db = get_db()

        if not all([username, email, password, confirm]):
            flash('All fields are required.', 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        elif db.execute('SELECT id FROM user WHERE username=?', (username,)).fetchone():
            flash('Username already taken.', 'error')
        elif db.execute('SELECT id FROM user WHERE email=?', (email,)).fetchone():
            flash('Email already registered.', 'error')
        else:
            db.execute(
                'INSERT INTO user (username, email, password_hash) VALUES (?,?,?)',
                (username, email, generate_password_hash(password))
            )
            db.commit()
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('auth_bp.login'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard_bp.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db   = get_db()
        user = db.execute('SELECT * FROM user WHERE username=?', (username,)).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id']  = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard_bp.dashboard'))

        flash('Invalid username or password.', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth_bp.login'))
