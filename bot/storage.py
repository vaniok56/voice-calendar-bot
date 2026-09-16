import json
import os
import tempfile
from pathlib import Path


class Storage:
    """Single-process JSON storage for access control."""

    def __init__(self, data_dir: Path, owner_id: int) -> None:
        self.data_dir = data_dir
        self.owner_id = owner_id
        self.access_path = data_dir / "access.json"
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)

    def _load_access(self) -> dict[str, list[int]]:
        if not self.access_path.exists():
            return {"admins": [], "users": []}
        try:
            raw = json.loads(self.access_path.read_text(encoding="utf-8"))
            admins, users = raw["admins"], raw["users"]
            if not all(
                isinstance(values, list)
                and all(isinstance(value, int) and value > 0 for value in values)
                for values in (admins, users)
            ):
                raise ValueError
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Invalid access file: {self.access_path}") from error
        return {"admins": admins, "users": users}

    def _save_access(self, access: dict[str, list[int]]) -> None:
        descriptor, temporary = tempfile.mkstemp(
            dir=self.data_dir, prefix=".access.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(access, file, indent=2)
                file.write("\n")
            os.replace(temporary, self.access_path)
            self.access_path.chmod(0o600)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    def role(self, user_id: int) -> str | None:
        if user_id == self.owner_id:
            return "owner"
        access = self._load_access()
        if user_id in access["admins"]:
            return "admin"
        if user_id in access["users"]:
            return "user"
        return None

    def is_allowed(self, user_id: int) -> bool:
        return self.role(user_id) is not None

    def is_admin(self, user_id: int) -> bool:
        return self.role(user_id) in {"owner", "admin"}

    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def list_users(self) -> list[tuple[int, str]]:
        access = self._load_access()
        result = [(self.owner_id, "owner")]
        result.extend((user_id, "admin") for user_id in access["admins"])
        result.extend((user_id, "user") for user_id in access["users"])
        return result

    def add_user(self, user_id: int) -> bool:
        if self.role(user_id) is not None:
            return False
        access = self._load_access()
        access["users"].append(user_id)
        self._save_access(access)
        return True

    def remove_user(self, user_id: int) -> bool:
        if user_id == self.owner_id:
            return False
        access = self._load_access()
        existed = user_id in access["admins"] or user_id in access["users"]
        access["admins"] = [value for value in access["admins"] if value != user_id]
        access["users"] = [value for value in access["users"] if value != user_id]
        if existed:
            self._save_access(access)
        return existed

    def promote(self, user_id: int) -> bool:
        if self.role(user_id) != "user":
            return False
        access = self._load_access()
        access["users"] = [value for value in access["users"] if value != user_id]
        access["admins"].append(user_id)
        self._save_access(access)
        return True

    def demote(self, user_id: int) -> bool:
        access = self._load_access()
        if user_id == self.owner_id or user_id not in access["admins"]:
            return False
        access["admins"] = [value for value in access["admins"] if value != user_id]
        access["users"].append(user_id)
        self._save_access(access)
        return True
