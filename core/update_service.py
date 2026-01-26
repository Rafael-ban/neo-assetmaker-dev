"""
更新服务 - GitHub Releases 自动更新
"""
import os
import re
import json
import logging
import tempfile
from typing import Optional, Tuple
from dataclasses import dataclass
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from PyQt6.QtCore import QThread, pyqtSignal, QObject

from config.constants import GITHUB_OWNER, GITHUB_REPO

logger = logging.getLogger(__name__)

# GitHub API Configuration
GITHUB_API_BASE = "https://api.github.com"
USER_AGENT = "ArknightsPassMaker-Updater/1.0"


@dataclass
class ReleaseInfo:
    """Release information from GitHub"""
    version: str           # e.g., "1.0.5"
    tag_name: str          # e.g., "v1.0.5"
    name: str              # Release title
    body: str              # Changelog/description (markdown)
    published_at: str      # ISO timestamp
    download_url: str      # Direct download URL for .exe installer
    download_size: int     # File size in bytes
    html_url: str          # Web URL to release page


class VersionComparer:
    """Version comparison utilities"""

    @staticmethod
    def parse_version(version_str: str) -> Tuple[int, ...]:
        """Parse version string to tuple of integers.

        Args:
            version_str: Version like "1.0.4" or "v1.0.4"

        Returns:
            Tuple of integers, e.g., (1, 0, 4)
        """
        # Remove 'v' prefix if present
        version_str = version_str.lstrip('vV')
        # Extract only numeric parts
        parts = re.findall(r'\d+', version_str)
        return tuple(int(p) for p in parts)

    @staticmethod
    def is_newer(remote_version: str, local_version: str) -> bool:
        """Check if remote version is newer than local.

        Args:
            remote_version: Version from GitHub release
            local_version: Current application version

        Returns:
            True if remote is newer
        """
        try:
            remote = VersionComparer.parse_version(remote_version)
            local = VersionComparer.parse_version(local_version)
            return remote > local
        except (ValueError, IndexError):
            return False


class UpdateCheckWorker(QThread):
    """Background worker for checking updates"""

    check_completed = pyqtSignal(object)  # ReleaseInfo or None
    check_failed = pyqtSignal(str)        # Error message

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current_version = current_version
        self._timeout = 10  # seconds

    def run(self):
        """Fetch latest release from GitHub API"""
        try:
            url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

            request = Request(url)
            request.add_header('User-Agent', USER_AGENT)
            request.add_header('Accept', 'application/vnd.github.v3+json')

            with urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode('utf-8'))

            # Parse release info
            tag_name = data.get('tag_name', '')
            version = tag_name.lstrip('vV')

            # Check if newer
            if not VersionComparer.is_newer(version, self._current_version):
                self.check_completed.emit(None)  # No update available
                return

            # Find Windows installer asset
            download_url = None
            download_size = 0

            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if name.endswith('_Setup.exe') or name.endswith('.exe'):
                    download_url = asset.get('browser_download_url')
                    download_size = asset.get('size', 0)
                    break

            if not download_url:
                self.check_failed.emit("未找到Windows安装包")
                return

            release_info = ReleaseInfo(
                version=version,
                tag_name=tag_name,
                name=data.get('name', f'v{version}'),
                body=data.get('body', ''),
                published_at=data.get('published_at', ''),
                download_url=download_url,
                download_size=download_size,
                html_url=data.get('html_url', '')
            )

            self.check_completed.emit(release_info)

        except HTTPError as e:
            if e.code == 404:
                self.check_failed.emit("未找到发布版本")
            elif e.code == 403:
                self.check_failed.emit("API请求次数超限，请稍后再试")
            else:
                self.check_failed.emit(f"服务器错误: {e.code}")
        except URLError as e:
            self.check_failed.emit(f"网络连接失败: {e.reason}")
        except json.JSONDecodeError:
            self.check_failed.emit("解析响应数据失败")
        except Exception as e:
            logger.exception("检查更新时发生错误")
            self.check_failed.emit(f"检查更新失败: {str(e)}")


class UpdateDownloadWorker(QThread):
    """Background worker for downloading updates"""

    progress_updated = pyqtSignal(int, str)   # (percentage, message)
    download_completed = pyqtSignal(str)       # Downloaded file path
    download_failed = pyqtSignal(str)          # Error message

    def __init__(self, release_info: ReleaseInfo, parent=None):
        super().__init__(parent)
        self._release_info = release_info
        self._cancelled = False

    def cancel(self):
        """Cancel the download"""
        self._cancelled = True

    def run(self):
        """Download the installer"""
        output_path = None
        try:
            url = self._release_info.download_url
            total_size = self._release_info.download_size

            # Create temp file in user's temp directory
            temp_dir = tempfile.gettempdir()
            filename = f"ArknightsPassMaker_v{self._release_info.version}_Setup.exe"
            output_path = os.path.join(temp_dir, filename)

            request = Request(url)
            request.add_header('User-Agent', USER_AGENT)

            self.progress_updated.emit(0, "正在连接服务器...")

            with urlopen(request, timeout=30) as response:
                downloaded = 0
                chunk_size = 8192  # 8KB chunks

                with open(output_path, 'wb') as f:
                    while True:
                        if self._cancelled:
                            f.close()
                            if os.path.exists(output_path):
                                os.remove(output_path)
                            self.download_failed.emit("下载已取消")
                            return

                        chunk = response.read(chunk_size)
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            size_mb = downloaded / (1024 * 1024)
                            total_mb = total_size / (1024 * 1024)
                            msg = f"已下载 {size_mb:.1f} / {total_mb:.1f} MB"
                        else:
                            percent = 50  # Unknown size
                            size_mb = downloaded / (1024 * 1024)
                            msg = f"已下载 {size_mb:.1f} MB"

                        self.progress_updated.emit(percent, msg)

            self.progress_updated.emit(100, "下载完成")
            self.download_completed.emit(output_path)

        except Exception as e:
            logger.exception("下载更新时发生错误")
            # Clean up partial download
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            self.download_failed.emit(f"下载失败: {str(e)}")


class UpdateService(QObject):
    """Update service - manages update checking and downloading"""

    # Signals for UI communication
    check_started = pyqtSignal()
    check_completed = pyqtSignal(object)  # ReleaseInfo or None
    check_failed = pyqtSignal(str)

    download_started = pyqtSignal()
    download_progress = pyqtSignal(int, str)
    download_completed = pyqtSignal(str)  # File path
    download_failed = pyqtSignal(str)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current_version = current_version
        self._check_worker: Optional[UpdateCheckWorker] = None
        self._download_worker: Optional[UpdateDownloadWorker] = None
        self._latest_release: Optional[ReleaseInfo] = None

    @property
    def is_checking(self) -> bool:
        return self._check_worker is not None and self._check_worker.isRunning()

    @property
    def is_downloading(self) -> bool:
        return self._download_worker is not None and self._download_worker.isRunning()

    @property
    def latest_release(self) -> Optional[ReleaseInfo]:
        return self._latest_release

    def check_for_updates(self):
        """Start checking for updates in background"""
        if self.is_checking:
            return

        self._check_worker = UpdateCheckWorker(self._current_version, self)
        self._check_worker.check_completed.connect(self._on_check_completed)
        self._check_worker.check_failed.connect(self._on_check_failed)

        self.check_started.emit()
        self._check_worker.start()

    def download_update(self, release_info: ReleaseInfo = None):
        """Start downloading the update"""
        if self.is_downloading:
            return

        release = release_info or self._latest_release
        if not release:
            self.download_failed.emit("没有可下载的更新")
            return

        self._download_worker = UpdateDownloadWorker(release, self)
        self._download_worker.progress_updated.connect(self.download_progress.emit)
        self._download_worker.download_completed.connect(self._on_download_completed)
        self._download_worker.download_failed.connect(self._on_download_failed)

        self.download_started.emit()
        self._download_worker.start()

    def cancel_download(self):
        """Cancel ongoing download"""
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.cancel()

    def _on_check_completed(self, release_info: Optional[ReleaseInfo]):
        self._latest_release = release_info
        self.check_completed.emit(release_info)
        self._cleanup_check_worker()

    def _on_check_failed(self, error_msg: str):
        self.check_failed.emit(error_msg)
        self._cleanup_check_worker()

    def _on_download_completed(self, file_path: str):
        self.download_completed.emit(file_path)
        self._cleanup_download_worker()

    def _on_download_failed(self, error_msg: str):
        self.download_failed.emit(error_msg)
        self._cleanup_download_worker()

    def _cleanup_check_worker(self):
        if self._check_worker:
            self._check_worker.deleteLater()
            self._check_worker = None

    def _cleanup_download_worker(self):
        if self._download_worker:
            self._download_worker.deleteLater()
            self._download_worker = None
