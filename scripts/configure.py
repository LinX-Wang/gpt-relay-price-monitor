"""Create an authenticated deployment environment without storing plaintext passwords."""
import getpass,hashlib,secrets,os
from pathlib import Path
from urllib.parse import urlsplit
root=Path(__file__).resolve().parents[1];target=root/'.env'
if target.exists():raise SystemExit('.env already exists. Edit it directly to preserve current settings.')
origin=input('Public origin (https://monitor.example.com): ').strip().rstrip('/')
url=urlsplit(origin)
if url.scheme not in ('http','https') or not url.netloc or url.path or url.query or url.fragment or url.username or url.password:raise SystemExit('Enter a complete origin without a path or credentials.')
username=input('Administrator username: ').strip()
password=getpass.getpass('Administrator password (8+ characters): ')
if not username or any(c.isspace() or c in chr(34)+chr(39)+'=#' for c in username) or len(password)<8:raise SystemExit('Invalid username or password.')
if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords do not match.')
salt=secrets.token_hex(16);digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()
values={'APP_HOST':'127.0.0.1','APP_PORT':'8765','APP_PUBLIC_URL':origin,'APP_AUTH_REQUIRED':'1','APP_USERNAME':username,'APP_PASSWORD_HASH':'pbkdf2_sha256$'+salt+'$'+digest,'APP_SESSION_SECRET':secrets.token_hex(32)}
target.write_text(''.join(k+"='"+v+"'\n" for k,v in values.items()),encoding='utf-8');os.chmod(target,0o600)
print('Created .env. Keep it private and back it up together with your data.')
