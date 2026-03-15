import sqlite3
import json

db_path = r"C:\Users\ricky\AppData\Local\Programs\Python\Python311\Lib\site-packages\open_webui\data\webui.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute(
        "SELECT id, title, updated_at FROM chat ORDER BY updated_at DESC LIMIT 1"
    )
    chat = cursor.fetchone()
    if chat:
        chat_id = chat[0]
        title = chat[1]

        cursor.execute("SELECT chat FROM chat WHERE id=?", (chat_id,))
        chat_data = cursor.fetchone()

        with open(
            r"c:\Projects\LocalAI\Evelyn\tools\chat_dump.txt", "w", encoding="utf-8"
        ) as out:
            out.write(f"Latest Chat: {title} (ID: {chat_id})\n\n")
            if chat_data and chat_data[0]:
                data = json.loads(chat_data[0])
                messages = data.get("messages", [])
                if not messages:
                    messages = list(
                        data.get("history", {}).get("messages", {}).values()
                    )

                for msg in messages[-15:]:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    out.write(f"[{role.upper()}]:\n{content}\n")
                    out.write("-" * 40 + "\n")
    else:
        print("No chats found.")
except Exception as e:
    print("Database error:", e)
finally:
    conn.close()
