"""
SSH connection pooling and management for efficient remote operations.
"""

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

import paramiko

from .config import DEFAULT_TIMEOUT
from .logger import logger


class SSHConnectionPool:
    """Thread-safe SSH connection pool with automatic reconnection."""

    def __init__(self, max_connections: int = 5, connection_timeout: int = DEFAULT_TIMEOUT):
        self.max_connections = max_connections
        self.connection_timeout = connection_timeout
        self._connections: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self._last_used: Dict[str, float] = {}

    def _create_connection_key(self, host: str, port: int, user: str) -> str:
        """Create a unique key for connection caching."""
        return f"{user}@{host}:{port}"

    def _create_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
    ) -> paramiko.SSHClient:
        """Create a new SSH connection."""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_args = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": self.connection_timeout,
        }

        if key_file:
            # Try different key types
            key = None
            key_types = [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]

            for key_type in key_types:
                try:
                    key = key_type.from_private_key_file(key_file)
                    logger.debug(f"Successfully loaded {key_type.__name__} private key")
                    break
                except Exception:
                    continue

            if key is None:
                raise Exception(f"Unable to load private key from {key_file}")

            connect_args["pkey"] = key
        elif password:
            connect_args["password"] = password
        else:
            raise ValueError("Either password or key_file must be provided")

        ssh.connect(**connect_args)
        logger.debug(f"Created new SSH connection to {user}@{host}:{port}")
        return ssh

    def _is_connection_alive(self, ssh: paramiko.SSHClient) -> bool:
        """Check if SSH connection is still alive."""
        try:
            transport = ssh.get_transport()
            if transport is None or not transport.is_active():
                return False

            # Test with a simple command
            stdin, stdout, stderr = ssh.exec_command("echo alive", timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            return exit_status == 0
        except Exception:
            return False

    @contextmanager
    def get_connection(
        self,
        host: str,
        port: int,
        user: str,
        password: Optional[str] = None,
        key_file: Optional[str] = None,
    ):
        """Get a connection from the pool with automatic cleanup."""
        connection_key = self._create_connection_key(host, port, user)
        ssh = None

        try:
            with self._lock:
                # Check if we have a cached connection
                if connection_key in self._connections:
                    ssh = self._connections[connection_key]
                    if self._is_connection_alive(ssh):
                        self._last_used[connection_key] = time.time()
                        logger.debug(f"Reusing SSH connection to {connection_key}")
                    else:
                        # Connection is dead, remove it
                        logger.debug(f"Removing dead SSH connection to {connection_key}")
                        try:
                            ssh.close()
                        except Exception:
                            pass
                        del self._connections[connection_key]
                        if connection_key in self._last_used:
                            del self._last_used[connection_key]
                        ssh = None

                # Create new connection if needed
                if ssh is None:
                    ssh = self._create_connection(host, port, user, password, key_file)
                    self._connections[connection_key] = ssh
                    self._last_used[connection_key] = time.time()

            yield ssh

        except Exception as e:
            # If connection failed, remove it from pool
            with self._lock:
                if connection_key in self._connections:
                    try:
                        self._connections[connection_key].close()
                    except Exception:
                        pass
                    del self._connections[connection_key]
                    if connection_key in self._last_used:
                        del self._last_used[connection_key]
            raise e

    def cleanup_idle_connections(self, max_idle_time: int = 300):
        """Clean up connections that have been idle for too long."""
        current_time = time.time()
        with self._lock:
            to_remove = []
            for key, last_used in self._last_used.items():
                if current_time - last_used > max_idle_time:
                    to_remove.append(key)

            for key in to_remove:
                try:
                    self._connections[key].close()
                except Exception:
                    pass
                del self._connections[key]
                del self._last_used[key]
                logger.debug(f"Cleaned up idle SSH connection: {key}")

    def close_all(self):
        """Close all connections in the pool."""
        with self._lock:
            for ssh in self._connections.values():
                try:
                    ssh.close()
                except Exception:
                    pass
            self._connections.clear()
            self._last_used.clear()
        logger.debug("Closed all SSH connections")


# Global connection pool instance
ssh_pool = SSHConnectionPool()


@contextmanager
def get_ssh_connection(
    host: str, port: int, user: str, password: Optional[str] = None, key_file: Optional[str] = None
):
    """Convenience function to get SSH connection from global pool."""
    with ssh_pool.get_connection(host, port, user, password, key_file) as ssh:
        yield ssh


class SSHOperationManager:
    """Manages multiple SSH operations over a single connection."""

    def __init__(self, ssh: paramiko.SSHClient):
        self.ssh = ssh
        self.sftp = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass

    def exec_command(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> tuple:
        """Execute a command and return stdin, stdout, stderr."""
        return self.ssh.exec_command(command, timeout=timeout)

    def get_sftp(self) -> paramiko.SFTPClient:
        """Get SFTP client, creating it if necessary."""
        if self.sftp is None:
            self.sftp = self.ssh.open_sftp()
        return self.sftp

    def exec_command_with_status(self, command: str, timeout: int = DEFAULT_TIMEOUT) -> tuple:
        """Execute command and return (exit_status, stdout, stderr)."""
        stdin, stdout, stderr = self.exec_command(command, timeout)
        exit_status = stdout.channel.recv_exit_status()
        stdout_data = stdout.read().decode(errors="ignore").strip()
        stderr_data = stderr.read().decode(errors="ignore").strip()
        return exit_status, stdout_data, stderr_data
