"""PyInstaller 진입 스크립트. 패키지 import 경로를 그대로 유지하기 위해 napi.__main__.main만 호출한다."""
from napi.__main__ import main

if __name__ == "__main__":
    main()
