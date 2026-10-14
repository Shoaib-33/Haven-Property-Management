"""Initialize an empty Haven workspace for a Hugging Face demo Space."""
import os

from app import db
from app.db import now, transaction
from app.security import password_hash


def seed_demo():
    """Fictional sample data (landlord, tenant, owner and contractor logins) for public demos."""
    from manage import demo
    demo()
    seed_branding()


def seed_branding():
    """For a demo that works straight away: the company logo, the landlord signature and the starter templates."""
    from pathlib import Path
    import app.main  # loads the app first (the template module depends on it)
    from app.templates_docs import STARTERS
    static = Path(__file__).resolve().parent.parent / 'static' / 'brand'
    logo, signature = static / 'haven-residential-logo.png', static / 'landlord-signature-demo.png'
    with transaction() as conn:
        company = conn.execute('SELECT id FROM companies WHERE kvk=?', ('DEMO-0001',)).fetchone()
        admin = conn.execute('SELECT id,name FROM users WHERE email=?', ('admin@haven.local',)).fetchone()
        if not company or not admin:
            return
        if logo.exists():
            conn.execute('INSERT OR REPLACE INTO company_logos(company_id,mime,data,updated_at) VALUES(?,?,?,?)', (company['id'], 'image/png', logo.read_bytes(), now()))
        if signature.exists():
            conn.execute('INSERT OR REPLACE INTO company_signatures(company_id,signer_name,signer_title,mode,data,updated_at) VALUES(?,?,?,?,?,?)', (company['id'], admin['name'], 'Director', 'type', signature.read_bytes(), now()))
        for name, kind, body in STARTERS:
            if not conn.execute('SELECT 1 FROM doc_templates WHERE company_id=? AND name=?', (company['id'], name)).fetchone():
                conn.execute('INSERT INTO doc_templates(company_id,name,kind,body,created_by,created_at) VALUES(?,?,?,?,?,?)', (company['id'], name, kind, body, admin['id'], now()))


def bootstrap_admin():
    if os.environ.get('HAVEN_DEMO_SEED') == '1':
        seed_demo()
        return
    email = os.environ.get('HAVEN_BOOTSTRAP_ADMIN_EMAIL', '').strip().lower()
    password = os.environ.get('HAVEN_BOOTSTRAP_ADMIN_PASSWORD', '')
    if not email or not password:
        raise SystemExit(
            'Set HAVEN_BOOTSTRAP_ADMIN_EMAIL and '
            'HAVEN_BOOTSTRAP_ADMIN_PASSWORD as Space variables/secrets.'
        )
    if len(password) < 12:
        raise SystemExit('HAVEN_BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.')

    db.init_db()
    with transaction() as conn:
        if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
        name = os.environ.get('HAVEN_BOOTSTRAP_ADMIN_NAME', 'Workspace administrator').strip()
        company_name = os.environ.get('HAVEN_BOOTSTRAP_COMPANY_NAME', 'Haven Demo Workspace').strip()
        user_id = conn.execute(
            'INSERT INTO users(name,email,password_hash,global_admin) VALUES(?,?,?,1)',
            (name or 'Workspace administrator', email, password_hash(password)),
        ).lastrowid
        company_id = conn.execute(
            'INSERT INTO companies(name,created_at) VALUES(?,?)',
            (company_name or 'Haven Demo Workspace', now()),
        ).lastrowid
        conn.execute('INSERT INTO memberships(user_id,company_id,role) VALUES(?,?,?)',
                     (user_id, company_id, 'admin'))


if __name__ == '__main__':
    bootstrap_admin()
