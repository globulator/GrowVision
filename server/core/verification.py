"""Сервис верификации Telegram-кодов, инвайт-кодов, контактов и регистрации/авторизации."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from server.api.deps import format_utc_z
from server.config import Settings, get_settings
from server.db.models import InviteCode, TelegramContact, User, WebVerificationCode
from server.schemas.auth import (
    InviteCreateResponse,
    MeResponse,
    PendingCodeItem,
    SessionInfo,
)

_INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


class VerificationService:
    """Инкапсулирует логику Telegram-верификации, инвайтов, регистрации и управления сессиями."""

    @staticmethod
    def extract_numeric_tg_id(client_type: str, external_id: str | None) -> int | None:
        """Извлекает числовой telegram_id из external_id для telegram/web сессий."""
        if client_type not in ("telegram", "web") or not external_id:
            return None
        ext = external_id.strip()
        if ext.isdigit() or (ext.startswith("-") and ext[1:].isdigit()):
            return int(ext)
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
        tid = cls.extract_numeric_tg_id(user.client_type, user.external_id)
        if tid is None:
            return None
        return cls.get_telegram_username_map([user], db).get(tid)

    @classmethod
    def build_me_response(cls, user: User, db: Session) -> MeResponse:
        """Собирает ответ профиля текущей сессии."""
        return MeResponse(
            id=user.id,
            login=user.login,
            role=user.role,
            client_type=user.client_type,
            external_id=user.external_id,
            telegram_username=cls.get_telegram_username(user, db),
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
            telegram_username=tg_usernames.get(tid) if tid is not None else None,
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
    def resolve_telegram_contact(
        telegram_input: str, db: Session
    ) -> tuple[int, TelegramContact]:
        """Резолвит ввод пользователя (@username или числовой ID) в числовой telegram_id и объект TelegramContact."""
        val = telegram_input.strip()
        if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
            tid = int(val)
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == tid)
                .first()
            )
            if not contact:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
                )
            return tid, contact

        norm_username = val.lstrip("@").strip().lower()
        contact = (
            db.query(TelegramContact)
            .filter(TelegramContact.username == norm_username)
            .first()
        )
        if not contact:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
            )
        return contact.telegram_id, contact

    @classmethod
    def resolve_telegram_id(cls, telegram_input: str, db: Session) -> int:
        """Резолвит ввод пользователя (@username или числовой ID) в числовой telegram_id."""
        tid, _ = cls.resolve_telegram_contact(telegram_input, db)
        return tid

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
            # Если username уже привязан к другой записи, освобождаем
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

    @staticmethod
    def get_pending_codes(db: Session) -> list[PendingCodeItem]:
        """Возвращает очередь неотправленных кодов подтверждения для Telegram-бота."""
        now = datetime.now(UTC)
        codes = (
            db.query(WebVerificationCode)
            .filter(
                WebVerificationCode.is_delivered.is_(False),
                WebVerificationCode.is_verified.is_(False),
                WebVerificationCode.expires_at > now,
            )
            .order_by(WebVerificationCode.created_at.asc())
            .all()
        )
        return [
            PendingCodeItem(id=c.id, telegram_id=c.telegram_id, code=c.code)
            for c in codes
        ]

    @staticmethod
    def ack_code_delivery(db: Session, code_id: int) -> None:
        """Подтверждает доставку кода пользователю в Telegram."""
        rec = (
            db.query(WebVerificationCode)
            .filter(WebVerificationCode.id == code_id)
            .first()
        )
        if rec:
            rec.is_delivered = True
            rec.delivered_at = datetime.now(UTC)
            db.commit()

    @staticmethod
    def fail_code_delivery(db: Session, code_id: int, error: str | None) -> None:
        """Фиксирует ошибку доставки кода подтверждения Telegram-ботом."""
        rec = (
            db.query(WebVerificationCode)
            .filter(WebVerificationCode.id == code_id)
            .first()
        )
        if rec:
            err_msg = (error or "delivery_failed").strip()
            rec.delivery_error = err_msg[:255]
            db.commit()

    @staticmethod
    def get_code_status(db: Session, telegram_id: int) -> dict[str, Any]:
        """Проверяет статус доставки кода подтверждения для указанного пользователя."""
        now = datetime.now(UTC)
        rec = (
            db.query(WebVerificationCode)
            .filter(
                WebVerificationCode.telegram_id == telegram_id,
                WebVerificationCode.expires_at > now,
                WebVerificationCode.is_verified.is_(False),
            )
            .order_by(WebVerificationCode.created_at.desc())
            .first()
        )
        if not rec:
            return {"status": "not_found"}
        if rec.delivery_error:
            return {"status": "delivery_error", "error": rec.delivery_error}
        if rec.is_delivered:
            return {"status": "delivered"}
        return {"status": "pending"}

    @classmethod
    def start_web_registration(cls, db: Session, telegram_input: str) -> dict[str, Any]:
        """Шаг 1 регистрации: валидация Telegram пользователя и генерация 6-значного кода."""
        telegram_id, contact = cls.resolve_telegram_contact(telegram_input, db)

        recommended_name: str | None = None
        if contact:
            parts = [
                p for p in (contact.first_name, contact.last_name) if p and p.strip()
            ]
            if parts:
                recommended_name = " ".join(parts).strip()
            elif contact.username:
                recommended_name = f"@{contact.username}"

        now = datetime.now(UTC)
        expires = now + timedelta(minutes=10)
        code = f"{secrets.randbelow(1_000_000):06d}"

        ver_rec = WebVerificationCode(
            telegram_id=telegram_id,
            code=code,
            created_at=now,
            expires_at=expires,
            is_verified=False,
            is_delivered=False,
        )
        db.add(ver_rec)
        db.commit()

        return {
            "status": "code_sent",
            "telegram_id": telegram_id,
            "recommended_name": recommended_name,
            "expires_in": 600,
        }

    @staticmethod
    def verify_web_registration_code(
        db: Session, telegram_id: int, code: str
    ) -> dict[str, Any]:
        """Шаг 2 регистрации: проверка введённого 6-значного кода."""
        now = datetime.now(UTC)
        rec = (
            db.query(WebVerificationCode)
            .filter(
                WebVerificationCode.telegram_id == telegram_id,
                WebVerificationCode.code == code.strip(),
                WebVerificationCode.expires_at > now,
                WebVerificationCode.is_verified.is_(False),
            )
            .order_by(WebVerificationCode.created_at.desc())
            .first()
        )
        if not rec:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный или просроченный код подтверждения",
            )

        rec.is_verified = True
        rec.verified_at = now
        db.commit()

        return {"status": "verified", "telegram_id": telegram_id}

    @classmethod
    def complete_web_registration(
        cls,
        db: Session,
        telegram_id: int,
        verification_code: str,
        full_name_raw: str,
        access_code_raw: str,
        settings: Settings | None = None,
    ) -> tuple[User, str, str]:
        """Шаг 3 регистрации: проверка верификации кода, инвайта/пароля и создание/обновление web-пользователя."""
        now = datetime.now(UTC)
        ver = (
            db.query(WebVerificationCode)
            .filter(
                WebVerificationCode.telegram_id == telegram_id,
                WebVerificationCode.code == verification_code.strip(),
                WebVerificationCode.is_verified.is_(True),
                WebVerificationCode.expires_at > now,
            )
            .first()
        )
        if not ver:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Код подтверждения не верифицирован или истёк",
            )

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

        ext_id = str(telegram_id)

        user = (
            db.query(User)
            .filter(
                User.client_type == "web",
                User.external_id == ext_id,
            )
            .first()
        )
        if not user:
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
        else:
            user.login = full_name
            user.role = role
            user.is_active = True
            user.authorized_at = now
            user.last_active_at = now
            user.logged_out_at = None

        if matched_invite:
            matched_invite.used_at = now
            matched_invite.used_by_user_id = user.id

        db.commit()
        return user, role, full_name

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

        # Проверка прав в зависимости от типа клиента
        if client_type == "cli":
            # В CLI принимается ТОЛЬКО пароль разработчика
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
            # Для Telegram: проверяем пароль разработчика или одноразовый код
            if cfg.developer_password and secrets.compare_digest(
                pwd_bytes, cfg.developer_password.encode("utf-8")
            ):
                role = "developer"
            else:
                # Ищем активный неиспользованный одноразовый код
                invite = cls.find_active_invite(db, password, now=now)
                if invite:
                    role = invite.role
                    matched_invite = invite
                elif cfg.biologist_password and secrets.compare_digest(
                    pwd_bytes, cfg.biologist_password.encode("utf-8")
                ):
                    # Поддержка постоянного пароля биолога (legacy/fallback)
                    role = "biologist"
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Неверный пароль или одноразовый код",
                    )

        # Поиск существующего пользователя по (client_type, external_id)
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
            # Новый пользователь: если login не передан, НЕ сжигаем код и запрашиваем имя
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
            # Пользователь уже существует: подтягиваем сохранённый login, если не передан новый
            if not resolved_login:
                resolved_login = user.login
            user.login = resolved_login
            user.role = role
            user.is_active = True
            user.authorized_at = now
            user.last_active_at = now
            user.logged_out_at = None

        # Если использован одноразовый код — помечаем его
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
        """Выполняет веб-вход по Telegram и коду доступа, возвращая обновлённого User."""
        # 1. Резолв пользователя Telegram
        telegram_input = telegram_input_raw.strip()
        contact: TelegramContact | None = None
        if telegram_input.isdigit() or (
            telegram_input.startswith("-") and telegram_input[1:].isdigit()
        ):
            telegram_id = int(telegram_input)
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.telegram_id == telegram_id)
                .first()
            )
        else:
            norm_username = telegram_input.lstrip("@").strip().lower()
            contact = (
                db.query(TelegramContact)
                .filter(TelegramContact.username == norm_username)
                .first()
            )
            if not contact:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
                )
            telegram_id = contact.telegram_id

        # 2. Проверка кода доступа (DEVELOPER_PASSWORD или активный InviteCode биолога)
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

        # 3. Синхронизация имени пользователя из TelegramContact
        ext_id = str(telegram_id)
        full_name = ""
        if contact:
            parts = [
                p for p in (contact.first_name, contact.last_name) if p and p.strip()
            ]
            if parts:
                full_name = " ".join(parts).strip()
            elif contact.username:
                full_name = f"@{contact.username}"

        if not full_name:
            user_existing = (
                db.query(User)
                .filter(
                    User.client_type.in_(["web", "telegram"]),
                    User.external_id == ext_id,
                )
                .first()
            )
            if user_existing and user_existing.login:
                full_name = user_existing.login
            else:
                full_name = f"User {telegram_id}"

        # 4. Проверка существования сессии пользователя в таблице users
        user = (
            db.query(User)
            .filter(
                User.client_type == "web",
                User.external_id == ext_id,
            )
            .first()
        )
        if not user:
            if not contact:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Пользователь с таким Telegram не найден. Пожалуйста, откройте Telegram-бота GrowVision, нажмите /start и повторите попытку.",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="telegram_verification_required",
            )

        if not user.login:
            user.login = full_name
        user.role = role
        user.is_active = True
        user.logged_out_at = None
        user.last_active_at = now

        if matched_invite:
            matched_invite.used_at = now
            matched_invite.used_by_user_id = user.id

        db.commit()
        return user

    @classmethod
    def create_invite(
        cls, db: Session, created_by_user_id: int
    ) -> InviteCreateResponse:
        """Генерирует одноразовый инвайт-код для биолога (срок действия 10 минут)."""
        now = datetime.now(UTC)
        expires = now + timedelta(minutes=10)

        # Генерируем уникальный код
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


verification_service = VerificationService()
generate_invite_code = verification_service.generate_invite_code
resolve_telegram_contact = verification_service.resolve_telegram_contact
resolve_telegram_id = verification_service.resolve_telegram_id

__all__ = [
    "VerificationService",
    "generate_invite_code",
    "resolve_telegram_contact",
    "resolve_telegram_id",
    "verification_service",
]
