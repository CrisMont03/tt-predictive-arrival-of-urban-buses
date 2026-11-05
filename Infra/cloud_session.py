"""Short-lived operator credentials from an existing gcloud session; no token files."""
from datetime import datetime, timedelta
import subprocess
from google.oauth2.credentials import Credentials


def credentials():
    def refresh(request, scopes=None):
        result = subprocess.run(['gcloud','auth','print-access-token','--quiet'],
                                capture_output=True,text=True,check=True)
        token = result.stdout.strip()
        if not token:
            raise ValueError('La sesión gcloud no devolvió credenciales')
        return token, datetime.utcnow() + timedelta(minutes=5)
    return Credentials(token=None,scopes=['https://www.googleapis.com/auth/cloud-platform'],refresh_handler=refresh)
