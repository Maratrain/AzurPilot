import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from module.notify import handle_notify

CONFIG = """\
provider: 
token: 
"""

CONTENT = """\
您好，您的公网IP遭到泄露，请及时添加密码。联系我们：qm点qq点com斜杠q斜杠6quSiiFMBi

您好，您的公網IP遭到洩露，請及時新增密碼。聯絡我們：qm點qq點com斜槓q斜槓6quSiiFMBi

Hello, your public IP has been exposed. Please add a password as soon as possible. Contact us: qm dot qq dot com slash q slash 6quSiiFMBi

こんにちは、あなたのグローバルIPが公開されています。早急にパスワードを設定してください。お問い合わせ：qm ドット qq ドット com スラッシュ q スラッシュ 6quSiiFMBi
"""


while True:
    handle_notify(CONFIG, title="AzurPilot OR Alas", content=CONTENT)
    time.sleep(0.5)
