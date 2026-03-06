import os

# uploads/94 하위 전체 파일 목록
for root, dirs, files in os.walk('uploads/94'):
    for f in files:
        path = os.path.join(root, f)
        size = os.path.getsize(path)
        print(f'  {path} ({size:,} bytes)')
