# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import dataclasses


DEFAULT_CONFIG_SERVICE_HOST = "127.0.0.1"
DEFAULT_CONFIG_SERVICE_PORT = 37300


@dataclasses.dataclass(frozen=True, slots=True)
class ConfigServiceAddress:
    """描述一次配置服务实例的不可变访问地址。"""

    host: str
    port: int

    @property
    def base_url(self) -> str:
        """返回配置服务根地址。"""
        return f"http://{self.host}:{self.port}"

    def endpoint(self, path: str) -> str:
        """拼接配置服务接口地址。"""
        return f"{self.base_url}/{str(path or '').lstrip('/')}"


if __name__ == '__main__':
    pass
