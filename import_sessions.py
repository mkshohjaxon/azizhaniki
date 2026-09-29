"""
sessions/ papkadagi .session fayllarni bir martalik tekshirib bazaga qo'shadi
(API ishlamayotgan paytda qo'lda ishlatish uchun).

venv/bin/python import_sessions.py            # sessions/ papka
venv/bin/python import_sessions.py boshqa/    # boshqa papka
"""
import asyncio
import sys

from account_manager import SESSIONS_DIR, AccountManager


async def main(folder: str):
    m = AccountManager()
    await m.init()
    try:
        results = await m.scan_folder(folder)
        ok = sum(r["ok"] for r in results)
        print(f"\nJami: {ok}/{len(results)} ta qo'shildi")
    finally:
        await m.shutdown()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else SESSIONS_DIR))
