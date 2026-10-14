# Unity로 전달하는 최신 3DGS 결과

이 폴더는 3D 담당자가 만든 결과를 웹·Unity 통합에 전달하는 경로다.
`latest.ply`는 현재 사용할 Gaussian Splat 결과이고, `latest.json`은 표시 이름과
파일 경로·SHA-256·출처 정보를 담는다. 영상에서 3DGS를 생성하는 과정은 팀원의
재구성 파이프라인에서 수행한다. 이 저장소의 SuperSplat 편집기가 재구성 엔진을
대신하는 것은 아니다.

최초 결과는 Unity 저장소의 `Assets/pointcloud_masked.ply`다. `latest.json`의
`bootstrapSource`에 최초 파일의 저장소·커밋·경로·SHA-256을 기록했다.

## 새 결과 게시

재구성 작업 또는 SuperSplat 편집·내보내기가 끝난 뒤 저장소 루트에서 실행한다.
Python 3.10 이상이면 추가 패키지는 필요 없다.

```bash
python3 scripts/publish_reconstruction.py /path/to/result.ply --name "딸기 3DGS"
git add reconstruction/latest.ply reconstruction/latest.json
git commit -m "Update strawberry reconstruction"
git push origin main
```

위 push는 현재 브랜치가 `main`인 경우다. 팀의 브랜치·PR 절차가 있으면 같은 두 파일을
한 커밋으로 올리고 `main`에 반영한다. 파일만 먼저 검증하려면 다음을 실행한다.

```bash
python3 scripts/publish_reconstruction.py /path/to/result.ply --check
```

도구는 원본을 삭제하거나 변경하지 않는다. binary little endian·float32 형식과
`x/y/z`, `f_dc_0..2`, `opacity`, `scale_0..2`, `rot_0..3` 속성 및 실제 데이터 길이를
검사한다. 일반 XYZ 포인트 클라우드, 메시 PLY, 압축 PLY 또는 손상된 파일은 거절한다.
SuperSplat에서는 통합용으로 비압축 Gaussian PLY를 내보낸다.

임시 복사본을 검증한 뒤 각 결과 파일을 원자적으로 교체한다. `latest.json`은 마지막에
갱신하며 두 파일을 같은 Git 커밋으로 게시한다. 메타데이터의 `sourceProvidedFile`에는
원본 파일명만 기록하며 로컬 절대 경로는 저장하지 않는다. 초기 파일과 내용이 달라지면
`bootstrapSource`를 제거하여 새 결과를 Unity 초기 파일로 잘못 표시하지 않는다.
이전 출처는 Git 기록에 남는다.

## main에서 웹까지

상시 갱신 프로세스가 실행 중이면 `main` 변경 감지 → 저장소별 커밋 고정 → PLY와
manifest 해시 검사 → Unity의 기존 PLY 경로에 복사(`.meta` GUID 유지) → Linux Vulkan
빌드·실행 → 실제 Unity 화면 수신 순서로 반영한다. `sourceCommit`은 이 서버 준비
과정에서 기록한다. PLY 파일만 바꿔놓고 이미 실행 중인 Unity가 갱신됐다고 처리하지 않는다.

웹은 설정된 최신 PLY 해시와 실제 Unity가 보내는 모델 해시·출처 커밋을 대조한다.
빌드 실패·실행 중단·오래된 프레임·모델 불일치는 최신 장면 적용 완료로 표시하지 않는다.
코드를 push한 사실과 서버에서 새 화면이 확인된 사실은 별개다.

도구의 파일 보존·형식 검사 테스트:

```bash
python3 -m unittest discover -s tests -p 'test_publish_reconstruction.py' -v
```
