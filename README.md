# Muse Mailbox

Ứng dụng Windows 10/11 quản lý tập trung các hộp thư **TempMail.Plus**.
Viết bằng Python + PySide6, lưu danh sách và tiến độ trong SQLite, lưu PIN và địa chỉ
bí mật trong **Windows Credential Manager**. Đóng gói thành `.exe` bằng PyInstaller.

## Luồng hoạt động

```
Ứng dụng quản lý hộp thư trên Windows
├── Thêm hộp thư đang có ─────────────────────────────┐
└── Tạo một lô hộp thư                                │
    └── Nhập số lượng và chọn thời hạn                │
        └── Có API tạo hộp thư đã xác minh?           │
            ├── Có  → ứng dụng tự tạo từng hộp thư    │
            └── Chưa → mở TempMail.Plus để người dùng │
                       tự tạo từng hộp                │
                └── Ghi nhận từng địa chỉ đã tạo ─────┤
                                                      ▼
                                  Danh sách hộp thư trên máy
                                  ├── Chọn địa chỉ để mở hộp thư
                                  └── Xem thời hạn và trạng thái PIN
```

## Giao diện

| Khi bạn thao tác | Ứng dụng thực hiện |
| --- | --- |
| **Thêm hộp thư đang có** | Nhập địa chỉ nhận thư đang hiển thị ở trang chính. Có ô riêng cho **địa chỉ bí mật** của đúng hộp đó (và PIN nếu có). Sau khi thêm, ứng dụng đọc cài đặt từ TempMail.Plus. |
| **Tạo một lô** | Ví dụ nhập 20, chọn 7 ngày. Thanh tiến độ đếm theo số hộp **thực sự đã tạo** (vd 12/20). Mỗi hộp được ghi vào SQLite ngay khi tạo xong, nên dừng giữa chừng thì 12 hộp vẫn có trong danh sách. |
| **Cài đặt** | Mặc định 7 ngày, tuỳ chọn **Đặt PIN tắt**. Nhãn **Đã áp dụng** chỉ hiện khi ứng dụng đọc lại được cài đặt đã lưu phía TempMail.Plus. |
| **Quản lý và đọc thư** | Bên trái là danh sách địa chỉ, mỗi dòng có nút **Sao chép**. Chọn một dòng để mở đúng hộp thư; thư tự làm mới mỗi 20 giây. Địa chỉ bí mật và PIN được ẩn (bấm *Hiện* khi cần). |

### Tạo hộp thư tự động hoạt động thế nào

TempMail.Plus không có bước "đăng ký" hộp thư: một địa chỉ tồn tại ngay khi có tên. Thứ
cần lưu phía trang là **cài đặt** của hộp (thời hạn, PIN). Ứng dụng dùng đúng các endpoint
mà trang web gọi (lấy từ mã JavaScript công khai của trang):

| Endpoint | Dùng để |
| --- | --- |
| `POST /api/box` (`email`, `ttl_minutes`, `pin`) | Lưu thời hạn và PIN |
| `GET /api/box` | Đọc lại cài đặt → quyết định "Đã áp dụng" |
| `GET /api/box/hidden` | Lấy địa chỉ bí mật |
| `GET /api/mails`, `GET /api/mails/<id>` | Đọc thư |

Một hộp trong lô chỉ được đếm khi `GET /api/box` trả về đúng thời hạn vừa đặt.

**Captcha:** trang có thể đòi reCAPTCHA khi lưu cài đặt. Lô được gửi qua trình duyệt tích hợp
(QtWebEngine) mở đúng trang tempmail.plus; khi trang đòi captcha, ô "Tôi không phải là người máy"
hiện ngay trong hộp thoại Tạo lô để bạn tự tích, rồi lô chạy tiếp. Ứng dụng không tự giải
captcha. Nếu bấm **Bỏ qua** hoặc **Dừng**, các hộp đã tạo vẫn được giữ và có nút
**Tạo tiếp N hộp còn lại**. Đây là API không chính thức nên có thể thay đổi bất cứ lúc nào.

## Chạy từ mã nguồn

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python run_app.py
```

## Build file .exe

```bat
build_windows.bat
```

Kết quả: `dist\MuseMailbox\MuseMailbox.exe`. Bộ cài đặt (tuỳ chọn) dùng Inno Setup:
`iscc packaging\installer.iss`.

GitHub Actions (`.github/workflows/build-windows.yml`) tự chạy test, build `.exe`, tạo bản
portable `.zip` và bộ cài đặt; đẩy tag `v1.0.0` để tạo Release.

## Tự cập nhật

Ứng dụng kiểm tra bản mới trên GitHub Releases của repo này khi mở và mỗi 6 giờ (tắt được
trong **Cài đặt**). Khi có bản mới: hỏi người dùng → tải `MuseMailbox-Setup-X.Y.Z.exe` →
kiểm tra SHA-256 → cài đè ở chế độ im lặng → tự mở lại. Bản portable thì mở trang tải về.

**Cách phát hành bản mới** (máy người dùng sẽ tự nhận):

1. Sửa `__version__` trong `muse/__init__.py`, ví dụ `1.2.0`.
2. Thêm mục `## 1.2.0` vào `CHANGELOG.md` (nội dung này hiện trong hộp thoại cập nhật).
3. Commit và push lên `main` (hoặc nhánh `claude/nifty-feynman-x1nonn`).

GitHub Actions sẽ build và tự tạo Release `v1.2.0` nếu phiên bản này chưa có Release.
Vài phút sau, ứng dụng trên máy người dùng sẽ hỏi cập nhật. Push mà không đổi phiên bản
thì chỉ build thử, không phát hành. (Đẩy tag `v1.2.0` cũng phát hành được như trước.)

## Dữ liệu được lưu ở đâu

| Dữ liệu | Vị trí |
| --- | --- |
| Danh sách hộp thư, tiến độ lô, cài đặt | `%LOCALAPPDATA%\MuseMailbox\mailboxes.db` (SQLite) |
| PIN, địa chỉ bí mật | Windows Credential Manager, mục `MuseMailbox` |

Đặt biến môi trường `MUSE_HOME` để dùng thư mục dữ liệu khác.

## Cấu trúc

```
muse/
  core/api.py       client TempMail.Plus
  core/batch.py     tạo một lô, ghi từng hộp ngay khi tạo xong
  core/storage.py   SQLite
  core/secrets.py   Windows Credential Manager (keyring)
  ui/               giao diện PySide6
tests/              kiểm thử với máy chủ giả lập
packaging/          PyInstaller spec, Inno Setup, icon
```
