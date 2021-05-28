from setuptools import find_packages, setup


setup_requires = ("setuptools_scm",)


install_requires = (
    "aiohttp==3.7.4",
    "neuro_auth_client==21.5.17",
    "platform-logging==21.5.13",
    "aiohttp-cors==0.7.0",
    "aiozipkin==1.1.0",
    "sentry-sdk==1.1.0",
    "marshmallow==3.12.1",
    "aiohttp-apispec==2.2.1",
    "alembic==1.6.4",
    "psycopg2-binary==2.8.6",
    "asyncpgsa==0.27.1",
    "sqlalchemy~=1.3.0",
)

setup(
    name="platform-service-accounts-api",
    use_scm_version={
        "git_describe_command": "git describe --dirty --tags --long --match v*.*.*",
    },
    url="https://github.com/neuro-inc/platform-service-accounts-api",
    packages=find_packages(),
    install_requires=install_requires,
    setup_requires=setup_requires,
    python_requires=">=3.7",
    entry_points={
        "console_scripts": [
            "platform-service-accounts-api=platform_service_accounts_api.api:main"
        ]
    },
    zip_safe=False,
)
