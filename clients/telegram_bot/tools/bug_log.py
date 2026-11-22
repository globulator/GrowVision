import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _read_bug_notes(logs_dir: Path) -> list[dict[str, Any]]:
    """Считывает все записи баг-репортов из bug_notes.jsonl."""
    notes_file = logs_dir / "bug_notes.jsonl"
    if not notes_file.exists():
        return []

    notes = []
    with open(notes_file, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                notes.append(json.loads(line_str))
            except json.JSONDecodeError:
                continue
    return notes


def cmd_list(logs_dir: Path) -> int:
    """Выводит список всех баг-заметок."""
    notes = _read_bug_notes(logs_dir)
    if not notes:
        print("Заметок о багах не найдено.")
        return 0

    print(f"Всего заметок о багах: {len(notes)}")
    print("-" * 70)
    for n in notes:
        bug_tag = n.get("bug_tag") or f"BUG-LOG-{n.get('bug_id', '?')}"
        ts = n.get("timestamp", "—")
        uid = n.get("user_id", "—")
        state = n.get("state") or "None"
        note = n.get("note", "").replace("\n", " ")
        print(f"[{bug_tag}] {ts} | user: {uid} | state: {state}")
        print(f"  Текст: {note}")
    return 0


def cmd_show(bug_id: int, logs_dir: Path) -> int:
    """Выводит подробный контекст баг-заметки с 20 предшествующими событиями."""
    notes = _read_bug_notes(logs_dir)
    target = next((n for n in notes if n.get("bug_id") == bug_id), None)
    if not target:
        print(f"Заметка BUG-LOG-{bug_id} не найдена.", file=sys.stderr)
        return 1

    bug_tag = target.get("bug_tag") or f"BUG-LOG-{target.get('bug_id')}"
    print(f"=== {bug_tag} ===")
    print(f"Метка времени: {target.get('timestamp')}")
    print(f"Пользователь:  {target.get('user_id')}")
    print(f"FSM состояние: {target.get('state')}")
    print(f"Заметка:       {target.get('note')}")
    print("\n--- Предшествующие события (до 20) ---")

    recent = target.get("recent_events", [])
    if not recent:
        print("  (предшествующие события отсутствуют)")
    else:
        for i, ev in enumerate(recent, 1):
            ts = ev.get("timestamp", "—")
            etype = ev.get("event_type", "event")
            uid = ev.get("user_id", "—")
            state = ev.get("state") or ""
            state_str = f" [state={state}]" if state else ""
            data = ev.get("data", {})
            data_str = json.dumps(data, ensure_ascii=False) if data else ""
            print(f"{i:2d}. [{ts}] {etype}{state_str} (uid: {uid}): {data_str}")

    return 0


def cmd_tail(n: int, logs_dir: Path) -> int:
    """Выводит последние n событий из журналов."""
    if not logs_dir.exists():
        print("Журнал событий пуст.")
        return 0

    log_files = sorted(logs_dir.glob("events_*.jsonl"))
    records: list[dict[str, Any]] = []

    # Читаем из суточных логов событий
    for lf in log_files:
        with open(lf, encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    try:
                        records.append(json.loads(line_str))
                    except json.JSONDecodeError:
                        continue

    # Если суточных логов нет, проверяем bug_notes.jsonl
    if not records:
        notes = _read_bug_notes(logs_dir)
        records.extend(notes)

    if not records:
        print("Журнал событий пуст.")
        return 0

    tail_records = records[-n:]
    print(f"Последние {len(tail_records)} событий:")
    print("-" * 70)
    for ev in tail_records:
        ts = ev.get("timestamp", "—")
        etype = ev.get("event_type", "event")
        uid = ev.get("user_id", "—")
        state = ev.get("state") or ""
        state_str = f" [state={state}]" if state else ""
        data = ev.get("data") or {
            k: v
            for k, v in ev.items()
            if k
            not in (
                "timestamp",
                "event_type",
                "user_id",
                "state",
                "recent_events",
            )
        }
        data_str = json.dumps(data, ensure_ascii=False)
        print(f"[{ts}] {etype}{state_str} (uid: {uid}): {data_str}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m clients.telegram_bot.tools.bug_log",
        description="Просмотр баг-заметок и журнала событий Telegram-бота",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=DEFAULT_LOGS_DIR,
        help="Путь к каталогу с логами (по умолчанию clients/telegram_bot/logs)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list", help="Список всех сохранённых баг-заметок")

    # show <N>
    show_parser = subparsers.add_parser(
        "show", help="Подробный контекст заметки с предшествующими событиями"
    )
    show_parser.add_argument(
        "bug_id",
        type=int,
        help="Номер заметки (например 1 для BUG-LOG-1)",
    )

    # tail [-n N]
    tail_parser = subparsers.add_parser("tail", help="Вывод последних событий журнала")
    tail_parser.add_argument(
        "-n",
        type=int,
        default=20,
        help="Количество событий для вывода (по умолчанию 20)",
    )

    args = parser.parse_args()

    if args.command == "list":
        sys.exit(cmd_list(args.logs_dir))
    elif args.command == "show":
        sys.exit(cmd_show(args.bug_id, args.logs_dir))
    elif args.command == "tail":
        sys.exit(cmd_tail(args.n, args.logs_dir))


if __name__ == "__main__":
    main()
