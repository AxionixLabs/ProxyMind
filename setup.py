# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
from pathlib import Path

from setuptools import (
    find_packages,
    setup,
)

# PEP 517 隔离执行 setup.py 时不保证源码根目录位于导入路径。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from infrastructure.sidecars.assets import runtime_asset_files
from metadata import const

with open('requirements.txt', encoding='utf-8') as f:
    requirements = f.read().splitlines()


asset_root = Path("sidecars")
assets = runtime_asset_files(asset_root)

setup(
    name=const.APP_NAME,
    version=const.APP_VERSION,
    url=const.APP_URL,
    author=const.AUTHOR,
    license=const.APP_LICENSE,
    author_email=const.EMAIL,
    description=const.APP_DESC,
    packages=find_packages(include=("agent", "agent.*", "protocol", "protocol.*", "frontends", "frontends.*",
                                   "infrastructure", "infrastructure.*", "observability", "observability.*",
                                   "metadata", "metadata.*", "sidecars")),
    include_package_data=False,
    py_modules=["mind", "composition"],
    package_data={"sidecars": [path.relative_to(asset_root).as_posix() for path in assets]},
    long_description=Path('README.md').read_text(encoding='utf-8'),
    long_description_content_type='text/markdown',
    install_requires=requirements,
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: JavaScript',
        'Programming Language :: Python :: 3.11',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows :: Windows 11',
        'Natural Language :: Chinese (Simplified)',
        'Natural Language :: English',
    ]
)


if __name__ == '__main__':
    pass
