# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

DEFAULT_CONFIG_SERVICE_HOST = "127.0.0.1"
DEFAULT_CONFIG_SERVICE_PORT = 37300


class ConfigServiceEndpoints(object):
    """保存配置服务运行时地址。"""

    def __init__(
        self,
        *,
        host: str = DEFAULT_CONFIG_SERVICE_HOST,
        port: int = DEFAULT_CONFIG_SERVICE_PORT
    ) -> None:
        self.host     = str(host or DEFAULT_CONFIG_SERVICE_HOST)
        self.port     = int(port)
        self.base_url = self._build_base_url()

    def configure(self, *, host: str, port: int) -> str:
        """更新配置服务运行时地址。"""
        self.host     = str(host or DEFAULT_CONFIG_SERVICE_HOST)
        self.port     = int(port)
        self.base_url = self._build_base_url()

        return self.base_url

    def endpoint(self, path: str) -> str:
        """拼接配置服务接口地址。"""
        return f"{self.base_url}/{str(path or '').lstrip('/')}"

    def _build_base_url(self) -> str:
        """根据 host 和 port 生成访问地址。"""
        return f"http://{self.host}:{self.port}"


config_service_endpoints = ConfigServiceEndpoints()


def config_service_base_url() -> str:
    """返回当前配置服务访问地址。"""
    return config_service_endpoints.base_url


if __name__ == "__main__":
    pass
