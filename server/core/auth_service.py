"""Сервис аутентификации, инвайт-кодов, контактов Telegram и управления сессиями."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z
from server.config import Settings, get_settings
from server.db.models import InviteCode, TelegramContact, User
from server.schemas.auth import (
    InviteCreateResponse,
    MeResponse,
    SessionInfo,
    TelegramLookupResponse,
)

_INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _split_full_name(full_name: str | None) -> tuple[str | None, str | None]:
    """Разбивает полное имя на (first_name, last_name)."""
    cleaned = (full_name or "").strip()
    if not cleaned:
        return None, None
    parts = cleaned.split(maxsplit=1)
    first = parts[0]
    last = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return first, last


class AuthService:
    """Инкапсулирует логику регистрации, входа, инвайтов, профиля и сессий."""

    @staticmethod
    def extract_numeric_tg_id(client_type: str, external_id: str | None) -> int | None:
        """Извлекает числовой telegram_id из external_id для telegram/web сессий."""
        if client_type not in ("telegram", "web") or not external_id:
            return None
        ext = external_id.strip()
        if ext.isdigit() or (ext.startswith("-") and ext[1:].isdigit()):
            return int(ext)
        return None

    @staticmethod
    def extract_tg_username_from_external_id(
        client_type: str, external_id: str | None
    ) -> str | None:
        """Извлекает @username из external_id, если он сохранён в виде @username."""
        if client_type not in ("telegram", "web") or not external_id:
            return None
        ext = external_id.strip()
        if ext.startswith("@") and len(ext) > 1:
            return ext[1:].strip().lower() or None
        return None

    @staticmethod
    def build_telegram_url(username: str | None, telegram_id: int | None) -> str | None:
        """Формирует кликабельную ссылку на профиль Telegram."""
        if username:
            clean = username.lstrip("@").strip()
            if clean:
                return f"https://t.me/{clean}"
        if telegram_id is not None:
            return f"tg://user?id={telegram_id}"
        return None

    @classmethod
    def get_telegram_username_map(
        cls, users: list[User], db: Session
    ) -> dict[int, str]:
        """Возвращает словарь {telegram_id: username} по списку пользователей."""
        tg_ids = {
            tid
            for u in users
            if (tid := cls.extract_numeric_tg_id(u.client_type, u.external_id))
            is not None
        }
        if not tg_ids:
            return {}
        contacts = (
            db.query(TelegramContact)
            .filter(TelegramContact.telegram_id.in_(tg_ids))
            .all()
        )
        return {
            int(c.telegram_id): c.username.lstrip("@") for c in contacts if c.username
        }

    @classmethod
    def get_telegram_username(cls, user: User, db: Session) -> str | None:
        """Возвращает telegram_username для одного пользователя, если он известен."""
        direct = cls.extract_tg_username_from_external_id(
            user.client_type, user.external_id
        )
        if direct:
            return direct
        tid = cls.extract_numeric_tg_id(user.client_type, user.external_id)
        if tid is None:
            return None
        return cls.get_telegram_username_map([user], db).get(tid)

    @classmethod
    def get_telegram_url(
        cls,
        user: User,
        db: Session | None = None,
        tg_usernames: dict[int, str] | None = None,
    ) -> str | None:
        """Вычисляет telegram_url для пользователя."""
        direct_username = cls.extract_tg_username_from_external_id(
            user.client_type, user.external_id
        )
        tid = cls.extract_numeric_tg_id(user.client_type, user.external_id)
        username = direct_username
        if not username and tid is not None:
            if tg_usernames is not None:
                username = tg_usernames.get(tid)
            elif db is not None:
                username = cls.get_telegram_username_map([user], db).get(tid)
        return cls.build_telegram_url(username, tid)

    @classmethod
    def build_me_response(cls, user: User, db: Session) -> MeResponse:
        """Собирает ответ профиля текущей сессии."""
        tg_usernames = cls.get_telegram_username_map([user], db)
        tid = cls.extract_numeric_tg_id(user.client_type, user.external_id)
        username = cls.extract_tg_username_from_external_id(
            user.client_type, user.external_id
        ) or (tg_usernames.get(tid) if tid is not None else None)
        return MeResponse(
            id=user.id,
            login=user.login,
            role=user.role,
            client_type=user.client_type,
            external_id=user.external_id,
            telegram_username=username,
            telegram_url=cls.build_telegram_url(username, tid),
            authorized_at=format_utc_z(user.authorized_at) or "",
            last_active_at=format_utc_z(user.last_active_at) or "",
        )

    @classmethod
    def build_session_info(
        cls,
        user: User,
        current_user_id: int,
        tg_usernames: dict[int, str],
    ) -> SessionInfo:
        """Собирает элемент списка сессий для разработчика."""
        tid = cls.extract_numeric_tg_id(user.client_type, user.external_id)
        username = cls.extract_tg_username_from_external_id(
            user.client_type, user.external_id
        ) or (tg_usernames.get(tid) if tid is not None else None)
        return SessionInfo(
            id=user.id,
            client_type=user.client_type,
            external_id=user.external_id,
            login=user.login,
            role=user.role,
            is_active=user.is_active,
            authorized_at=format_utc_z(user.authorized_at) or "",
            last_active_at=format_utc_z(user.last_active_at) or "",
            logged_out_at=format_utc_z(user.logged_out_at),
            telegram_username=username,
            telegram_url=cls.build_telegram_url(username, tid),
            is_current=user.id == current_user_id,
        )

    @staticmethod
    def generate_invite_code(length: int = 6) -> str:
        """Генерирует читаемый уникальный код из букв и цифр без похожих символов."""
        return "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(length))

    @staticmethod
    def find_active_invite(
        db: Session, raw_code: str, now: datetime | None = None
    ) -> InviteCode | None:
        """Ищет активный неиспользованный инвайт-код без учёта регистра."""
        check_now = now or datetime.now(UTC)
        return (
            db.query(InviteCode)
            .filter(
                InviteCode.code == raw_code.strip().upper(),
                InviteCode.used_at.is_(None),
                InviteCode.is_revoked.is_(False),
                InviteCode.expires_at > check_now,
            )
            .first()
        )

    @staticmethod
    def sync_telegram_contact(
        db: Session,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        """Создаёт или обновляет связку username -> telegram_id от Telegram-бота."""
        norm_username = (
            username.lstrip("@").strip().lower()
            if (username and username.strip())
            else None
        )

        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.telegram_id == telegram_id)
            .first()
        )
        now = datetime.now(UTC)
        if contact:
            contact.username = norm_username
            contact.first_name = first_name
            contact.last_name = last_name
            contact.updated_at = now
        else:
            if norm_username:
                existing_user = (
                    db.query(TelegramContact)
                    .filter(TelegramContact.username == norm_username)
                    .first()
                )
                if existing_user:
                    existing_user.username = None

            contact = TelegramContact(
                telegram_id=telegram_id,
                username=norm_username,
                first_name=first_name,
                last_name=last_name,
                updated_at=now,
            )
            db.add(contact)

        db.commit()

    @classmethod
    def lookup_telegram_contact(
        cls, db: Session, telegram_input: str
    ) -> TelegramLookupResponse:
        """Ищет контакт в TelegramContact или User для автоподстановки имени/фамилии."""
        val = (telegram_input or "").strip()
        if not val:
            return TelegramLookupResponse(found=False)

        is_numeric = val.isdigit() or (val.startswith("-") and val[1:].isdigit())
        contact: TelegramContact | None = None
        if is_numeric:
            tid = int(val)
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == tid)
                .first()
            )
            if contact:
                parts = [
                    p.strip()
                    for p in (contact.first_name, contact.last_name)
                    if p and p.strip()
                ]
                recommended = " ".join(parts) if parts else None
                return TelegramLookupResponse(
                    found=True,
                    telegram_id=contact.telegram_id,
                    username=contact.username,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    recommended_name=recommended,
                    telegram_url=cls.build_telegram_url(
                        contact.username, contact.telegram_id
                    ),
                )

            user = (
                db.query(User)
                .filter(
                    User.client_type.in_(["web", "telegram"]),
                    User.external_id == str(tid),
                )
                .order_by(User.last_active_at.desc())
                .first()
            )
            if user:
                fn, ln = _split_full_name(user.login)
                return TelegramLookupResponse(
                    found=True,
                    telegram_id=tid,
                    username=None,
                    first_name=fn,
                    last_name=ln,
                    recommended_name=user.login,
                    telegram_url=cls.build_telegram_url(None, tid),
                )
            return TelegramLookupResponse(found=False)

        norm_username = val.lstrip("@").strip().lower()
        if not norm_username:
            return TelegramLookupResponse(found=False)

        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.username == norm_username)
            .first()
        )
        if contact:
            parts = [
                p.strip()
                for p in (contact.first_name, contact.last_name)
                if p and p.strip()
            ]
            recommended = " ".join(parts) if parts else None
            return TelegramLookupResponse(
                found=True,
                telegram_id=contact.telegram_id,
                username=contact.username,
                first_name=contact.first_name,
                last_name=contact.last_name,
                recommended_name=recommended,
                telegram_url=cls.build_telegram_url(
                    contact.username, contact.telegram_id
                ),
            )

        user = (
            db.query(User)
            .filter(
                User.client_type.in_(["web", "telegram"]),
                User.external_id == f"@{norm_username}",
            )
            .order_by(User.last_active_at.desc())
            .first()
        )
        if user:
            fn, ln = _split_full_name(user.login)
            return TelegramLookupResponse(
                found=True,
                telegram_id=None,
                username=norm_username,
                first_name=fn,
                last_name=ln,
                recommended_name=user.login,
                telegram_url=cls.build_telegram_url(norm_username, None),
            )

        return TelegramLookupResponse(found=False)

    @staticmethod
    def resolve_web_telegram_external_id(
        db: Session,
        telegram_input: str | None = None,
        telegram_id: int | None = None,
    ) -> tuple[str, int | None, str | None]:
        """Преобразует ввод Telegram в (external_id, telegram_id, username)."""
        if telegram_id is not None:
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == telegram_id)
                .first()
            )
            return str(telegram_id), telegram_id, contact.username if contact else None

        val = (telegram_input or "").strip()
        if not val:
            return f"web:{uuid.uuid4()}", None, None

        if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
            tid = int(val)
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == tid)
                .first()
            )
            return str(tid), tid, contact.username if contact else None

        norm_username = val.lstrip("@").strip().lower()
        if not norm_username:
            return f"web:{uuid.uuid4()}", None, None

        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.username == norm_username)
            .first()
        )
        if contact:
            return str(contact.telegram_id), contact.telegram_id, norm_username
        return f"@{norm_username}", None, norm_username

    @staticmethod
    def ensure_no_telegram_conflict(
        db: Session,
        resolved_tid: int | None,
        resolved_username: str | None,
        exclude_user_id: int | None = None,
    ) -> None:
        """Проверяет, что telegram_id и @username не заняты другим web-аккаунтом."""
        candidate_ext_ids: set[str] = set()
        if resolved_tid is not None:
            candidate_ext_ids.add(str(resolved_tid))
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == resolved_tid)
                .first()
            )
            if contact and contact.username:
                candidate_ext_ids.add(f"@{contact.username.lstrip('@').lower()}")

        if resolved_username:
            norm_u = resolved_username.lstrip("@").strip().lower()
            candidate_ext_ids.add(f"@{norm_u}")
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.username == norm_u)
                .first()
            )
            if contact:
                candidate_ext_ids.add(str(contact.telegram_id))

        if not candidate_ext_ids:
            return

        query = db.query(User).filter(
            User.client_type == "web",
            User.external_id.in_(candidate_ext_ids),
        )
        if exclude_user_id is not None:
            query = query.filter(User.id != exclude_user_id)

        existing = query.first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Этот Telegram уже привязан к другому аккаунту",
            )

    @classmethod
    def complete_web_registration(
        cls,
        db: Session,
        telegram_raw: str | None,
        telegram_id: int | None,
        full_name_raw: str,
        access_code_raw: str,
        settings: Settings | None = None,
    ) -> tuple[User, str, str]:
        """Регистрация web-пользователя в 1 шаг без подтверждения кодом через Telegram."""
        now = datetime.now(UTC)
        cfg = settings or get_settings()
        access_code = access_code_raw.strip()
        role: str | None = None
        matched_invite: InviteCode | None = None

        if cfg.developer_password and secrets.compare_digest(
            access_code.encode("utf-8"), cfg.developer_password.encode("utf-8")
        ):
            role = "developer"
        else:
            matched_invite = cls.find_active_invite(db, access_code, now=now)
            if matched_invite:
                role = matched_invite.role or "biologist"
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Неверный или просроченный код приглашения / пароль доступа",
                )

        full_name = full_name_raw.strip()
        if not full_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Имя и Фамилия не могут быть пустыми",
            )

        ext_id, resolved_tid, resolved_username = cls.resolve_web_telegram_external_id(
            db, telegram_input=telegram_raw, telegram_id=telegram_id
        )
        cls.ensure_no_telegram_conflict(db, resolved_tid, resolved_username)

        user = User(
            client_type="web",
            external_id=ext_id,
            login=full_name,
            role=role,
            is_active=True,
            authorized_at=now,
            last_active_at=now,
            logged_out_at=None,
        )
        db.add(user)
        db.flush()

        if matched_invite:
            matched_invite.used_at = now
            matched_invite.used_by_user_id = user.id

        db.commit()
        db.refresh(user)
        return user, role, full_name

    @classmethod
    def update_me_profile(
        cls,
        db: Session,
        user: User,
        new_login: str,
        telegram_provided: bool,
        telegram_raw: str | None,
    ) -> User:
        """Обновляет имя и (опционально) Telegram текущего пользователя."""
        user.login = new_login
        if telegram_provided and user.client_type == "web":
            tg_val = (telegram_raw or "").strip()
            if not tg_val:
                if not user.external_id.startswith("web:"):
                    user.external_id = f"web:{uuid.uuid4()}"
            else:
                ext_id, resolved_tid, resolved_username = (
                    cls.resolve_web_telegram_external_id(db, telegram_input=tg_val)
                )
                cls.ensure_no_telegram_conflict(
                    db,
                    resolved_tid,
                    resolved_username,
                    exclude_user_id=user.id,
                )
                user.external_id = ext_id

        db.commit()
        db.refresh(user)
        return user

    @classmethod
    def authenticate_client_login(
        cls,
        db: Session,
        client_type: str,
        external_id: str,
        password: str,
        login: str | None,
        settings: Settings | None = None,
    ) -> dict[str, Any]:
        """Выполняет проверку пароля/инвайта и обновление сессии для POST /api/auth/login."""
        cfg = settings or get_settings()
        pwd_bytes = password.strip().encode("utf-8")
        now = datetime.now(UTC)

        role: str | None = None
        matched_invite: InviteCode | None = None

        if client_type == "cli":
            if cfg.developer_password and secrets.compare_digest(
                pwd_bytes, cfg.developer_password.encode("utf-8")
            ):
                role = "developer"
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Неверный пароль разработчика",
                )
            if not login or not login.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Для входа через CLI необходимо указать login",
                )
        else:
            if cfg.developer_password and secrets.compare_digest(
                pwd_bytes, cfg.developer_password.encode("utf-8")
            ):
                role = "developer"
            else:
                invite = cls.find_active_invite(db, password, now=now)
                if invite:
                    role = invite.role
                    matched_invite = invite
                elif cfg.biologist_password and secrets.compare_digest(
                    pwd_bytes, cfg.biologist_password.encode("utf-8")
                ):
                    role = "biologist"
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Неверный пароль или одноразовый код",
                    )

        user = (
            db.query(User)
            .filter(
                User.client_type == client_type,
                User.external_id == external_id,
            )
            .first()
        )

        resolved_login = login.strip() if (login and login.strip()) else None

        if not user:
            if not resolved_login:
                return {"needs_login": True, "role": role, "login": None}
            user = User(
                client_type=client_type,
                external_id=external_id,
                login=resolved_login,
                role=role,
                is_active=True,
                authorized_at=now,
                last_active_at=now,
                logged_out_at=None,
            )
            db.add(user)
            db.flush()
        else:
            if not resolved_login:
                resolved_login = user.login
            user.login = resolved_login
            user.role = role
            user.is_active = True
            user.authorized_at = now
            user.last_active_at = now
            user.logged_out_at = None

        if matched_invite:
            matched_invite.used_at = now
            matched_invite.used_by_user_id = user.id

        db.commit()
        return {"needs_login": False, "role": role, "login": resolved_login}

    @classmethod
    def authenticate_web_login(
        cls,
        db: Session,
        telegram_input_raw: str,
        access_code_raw: str,
        settings: Settings | None = None,
    ) -> User:
        """Выполняет веб-вход по Telegram (@username или ID) и коду доступа без бота."""
        telegram_input = telegram_input_raw.strip()
        candidate_ext_ids: list[str] = []

        if telegram_input.isdigit() or (
            telegram_input.startswith("-") and telegram_input[1:].isdigit()
        ):
            telegram_id = int(telegram_input)
            candidate_ext_ids.append(str(telegram_id))
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == telegram_id)
                .first()
            )
            if contact and contact.username:
                candidate_ext_ids.append(f"@{contact.username.lstrip('@').lower()}")
        else:
            norm_username = telegram_input.lstrip("@").strip().lower()
            if norm_username:
                candidate_ext_ids.append(f"@{norm_username}")
                contact = (
                    db.query(TelegramContact)
                    .filter(TelegramContact.username == norm_username)
                    .first()
                )
                if contact:
                    candidate_ext_ids.append(str(contact.telegram_id))

        access_code = access_code_raw.strip()
        cfg = settings or get_settings()
        now = datetime.now(UTC)
        role: str | None = None
        matched_invite: InviteCode | None = None

        if cfg.developer_password and secrets.compare_digest(
            access_code.encode("utf-8"), cfg.developer_password.encode("utf-8")
        ):
            role = "developer"
        else:
            matched_invite = cls.find_active_invite(db, access_code, now=now)
            if matched_invite:
                role = matched_invite.role or "biologist"
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Неверный или просроченный код приглашения / пароль разработчика",
                )

        user: User | None = None
        if candidate_ext_ids:
            user = (
                db.query(User)
                .filter(
                    User.client_type == "web",
                    User.external_id.in_(candidate_ext_ids),
                )
                .first()
            )

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Пользователь с таким Telegram не найден. Пожалуйста, зарегистрируйтесь.",
            )

        user.role = role
        user.is_active = True
        user.logged_out_at = None
        user.last_active_at = now

        if matched_invite:
            matched_invite.used_at = now
            matched_invite.used_by_user_id = user.id

        db.commit()
        db.refresh(user)
        return user

    @classmethod
    def create_invite(
        cls, db: Session, created_by_user_id: int
    ) -> InviteCreateResponse:
        """Генерирует одноразовый инвайт-код для биолога (срок действия 10 минут)."""
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=10)

        code = cls.generate_invite_code(6)
        while db.query(InviteCode).filter(InviteCode.code == code).first():
            code = cls.generate_invite_code(6)

        invite = InviteCode(
            code=code,
            role="biologist",
            created_by_user_id=created_by_user_id,
            created_at=now,
            expires_at=expires,
            is_revoked=False,
        )
        db.add(invite)
        db.commit()

        return InviteCreateResponse(
            id=invite.id,
            code=invite.code,
            role=invite.role,
            created_at=format_utc_z(invite.created_at) or "",
            expires_at=format_utc_z(invite.expires_at) or "",
        )

    @staticmethod
    def list_active_invites(db: Session) -> list[InviteCreateResponse]:
        """Возвращает список активных неиспользованных инвайт-кодов."""
        now = datetime.now(UTC)
        invites = (
            db.query(InviteCode)
            .filter(
                InviteCode.used_at.is_(None),
                InviteCode.is_revoked.is_(False),
                InviteCode.expires_at > now,
            )
            .order_by(InviteCode.created_at.desc())
            .all()
        )
        return [
            InviteCreateResponse(
                id=i.id,
                code=i.code,
                role=i.role,
                created_at=format_utc_z(i.created_at) or "",
                expires_at=format_utc_z(i.expires_at) or "",
            )
            for i in invites
        ]

    @staticmethod
    def revoke_invite(db: Session, invite_id: int) -> None:
        """Отзывает неиспользованный инвайт-код."""
        invite = db.query(InviteCode).filter(InviteCode.id == invite_id).first()
        if not invite:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Инвайт-код #{invite_id} не найден",
            )
        invite.is_revoked = True
        db.commit()

    @classmethod
    def list_sessions(cls, db: Session, current_user_id: int) -> list[SessionInfo]:
        """Возвращает список всех сессий пользователей (активных и неактивных)."""
        users = db.query(User).order_by(User.last_active_at.desc()).all()
        tg_usernames = cls.get_telegram_username_map(users, db)
        return [cls.build_session_info(u, current_user_id, tg_usernames) for u in users]

    @staticmethod
    def terminate_session(db: Session, session_id: int) -> None:
        """Принудительно завершает активную сессию пользователя (кик)."""
        target_user = db.query(User).filter(User.id == session_id).first()
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Сессия #{session_id} не найдена",
            )
        target_user.is_active = False
        target_user.logged_out_at = datetime.now(UTC)
        db.commit()

    @classmethod
    def rename_session_user(
        cls,
        db: Session,
        session_id: int,
        new_login: str,
        current_user_id: int,
    ) -> SessionInfo:
        """Изменяет отображаемое имя пользователя любой сессии (Developer)."""
        target_user = db.query(User).filter(User.id == session_id).first()
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Сессия #{session_id} не найдена",
            )
        target_user.login = new_login
        db.commit()
        db.refresh(target_user)
        tg_usernames = cls.get_telegram_username_map([target_user], db)
        return cls.build_session_info(target_user, current_user_id, tg_usernames)


auth_service = AuthService()
generate_invite_code = auth_service.generate_invite_code

__all__ = [
    "AuthService",
    "auth_service",
    "generate_invite_code",
]
