"""
Alf-E Backup Engine — cloud backup before every connector code commit.

Backs up /data to Backblaze B2 (or local tarball fallback) before any
file write in the self-building approval loop. If backup fails, the
commit is blocked — never risk data loss for a new connector.

Usage:
    from engine.backup import BackupEngine
    backup = BackupEngine()
    result = backup.run()
    if not result.success:
        raise RuntimeError(f"Backup failed: {result.error}")
"""

import os
import subprocess
import logging
import tarfile
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("alfe.backup")


@dataclass
class BackupResult:
    success: bool
    path: str = ""          # local tarball path or B2 remote path
    error: str = ""
    duration_s: float = 0.0


class BackupEngine:
    """Backs up /data before connector commits.

    Strategy (in order of preference):
    1. Backblaze B2 via rclone (if B2_BUCKET + rclone configured)
    2. Local tarball to /data/backups/ (always works, on-disk only)

    The agent calls backup.run() and gets a BackupResult. If success=False,
    the approval flow aborts — never write files without a backup.
    """

    def __init__(self):
        self.data_dir    = Path(os.environ.get("ALFE_DATA_DIR", "/data"))
        self.backup_dir  = self.data_dir / "backups"
        self.b2_bucket   = os.environ.get("B2_BUCKET", "")
        self.b2_prefix   = os.environ.get("B2_PREFIX", "alfe-backups")
        self.rclone_remote = os.environ.get("RCLONE_REMOTE", "b2")

    def run(self, label: str = "pre_commit") -> BackupResult:
        """Run a full backup. Returns BackupResult."""
        import time
        start = time.monotonic()

        # Always create local tarball first
        local_result = self._local_tarball(label)
        if not local_result.success:
            return local_result

        # Attempt cloud upload if configured
        if self.b2_bucket:
            cloud_result = self._upload_to_b2(local_result.path, label)
            if not cloud_result.success:
                logger.warning(
                    f"Cloud backup failed (local backup still OK): {cloud_result.error}"
                )
                # Return local result — local backup is better than nothing
                local_result.duration_s = time.monotonic() - start
                return local_result
            cloud_result.duration_s = time.monotonic() - start
            logger.info(f"Backup complete: {cloud_result.path} ({cloud_result.duration_s:.1f}s)")
            return cloud_result

        local_result.duration_s = time.monotonic() - start
        logger.info(f"Local backup complete: {local_result.path} ({local_result.duration_s:.1f}s)")
        return local_result

    def _local_tarball(self, label: str) -> BackupResult:
        """Create a .tar.gz of the data directory."""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename  = f"alfe_{label}_{timestamp}.tar.gz"
            dest      = self.backup_dir / filename

            with tarfile.open(dest, "w:gz") as tar:
                # Back up the SQLite DB and connector code
                for subdir in ["alfe_memory.db", "connectors_live"]:
                    src = self.data_dir / subdir
                    if src.exists():
                        tar.add(src, arcname=subdir)
                # Also back up the engine/connectors directory (the code itself)
                connectors_src = Path(__file__).parent / "connectors"
                if connectors_src.exists():
                    tar.add(connectors_src, arcname="engine_connectors")

            logger.info(f"Local tarball created: {dest}")
            return BackupResult(success=True, path=str(dest))

        except Exception as e:
            logger.error(f"Local backup failed: {e}")
            return BackupResult(success=False, error=str(e))

    def _upload_to_b2(self, local_path: str, label: str) -> BackupResult:
        """Upload local tarball to Backblaze B2 via rclone."""
        if not shutil.which("rclone"):
            return BackupResult(
                success=False,
                error="rclone not found — install rclone and configure B2 remote",
            )
        dest = f"{self.rclone_remote}:{self.b2_bucket}/{self.b2_prefix}/{Path(local_path).name}"
        try:
            result = subprocess.run(
                ["rclone", "copy", local_path, f"{self.rclone_remote}:{self.b2_bucket}/{self.b2_prefix}/"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return BackupResult(success=True, path=dest)
            return BackupResult(
                success=False,
                error=f"rclone exited {result.returncode}: {result.stderr.strip()}",
            )
        except subprocess.TimeoutExpired:
            return BackupResult(success=False, error="rclone timed out after 120s")
        except Exception as e:
            return BackupResult(success=False, error=str(e))

    def cleanup_old_local(self, keep: int = 10) -> int:
        """Delete old local tarballs, keeping the N most recent. Returns count deleted."""
        if not self.backup_dir.exists():
            return 0
        tarballs = sorted(self.backup_dir.glob("alfe_*.tar.gz"), key=lambda p: p.stat().st_mtime)
        to_delete = tarballs[:-keep] if len(tarballs) > keep else []
        for f in to_delete:
            try:
                f.unlink()
                logger.info(f"Deleted old backup: {f.name}")
            except Exception as e:
                logger.warning(f"Could not delete {f}: {e}")
        return len(to_delete)
