# Python Project Template / Python 프로젝트 템플릿

Project template with Ruff code formatting configuration / Ruff를 사용한 Python 코드 포맷팅 설정이 포함된 프로젝트 템플릿입니다.

## Configuration Files / 설정 파일

- [pyproject.toml](pyproject.toml) - Ruff configuration (Python 3.12, line length 88) / Ruff 설정
- [.gitignore](.gitignore) - Python project Git ignore file / Python 프로젝트용 Git 무시 파일

## GitHub Templates / GitHub 템플릿

- [`.github/workflows/format-check.yml`](.github/workflows/format-check.yml) - Code format check on PR / PR 생성 시 코드 포맷 검사
- [`.github/ISSUE_TEMPLATE/issue.md`](.github/ISSUE_TEMPLATE/issue.md) - Issue template / 이슈 템플릿
- [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md) - PR template / PR 템플릿
- [`.github/CODEOWNERS`](.github/CODEOWNERS) - Code reviewer configuration / 코드 리뷰어 설정

## Usage / 사용법

```bash
# Install Ruff / Ruff 설치
pip install ruff

# Format code / 코드 포맷팅
ruff format .

# Check format only / 포맷 검사만 수행
ruff format --check .

# Fix imports / import 정렬
ruff check --fix I .
```
