# Changelog

## 1.2.0
- Khi TempMail.Plus đòi captcha, lô **không dừng nữa**: ô "Tôi không phải là người máy"
  hiện ngay trong hộp thoại Tạo lô. Tích xong, lô tự chạy tiếp từ chỗ đang dở.
- Lô được gửi qua trình duyệt tích hợp mở đúng trang tempmail.plus (giống bạn thao tác trên web),
  cookie được giữ giữa các lần chạy.
- Dừng giữa chừng: nút "Tạo tiếp N hộp còn lại" để chạy nốt phần còn thiếu.
- Có thể tắt trình duyệt tích hợp trong Cài đặt.

## 1.1.0
- Tự cập nhật: khi mở ứng dụng và mỗi 6 giờ, kiểm tra bản phát hành mới trên GitHub.
  Có bản mới thì hỏi, tải bộ cài, kiểm tra SHA-256, cài đè và tự mở lại.
- Dữ liệu (danh sách hộp thư, PIN, địa chỉ bí mật) được giữ nguyên khi cập nhật.
- Nút "Kiểm tra cập nhật" trên thanh công cụ; có thể tắt tự kiểm tra trong Cài đặt.

## 1.0.0
- Phiên bản đầu tiên: quản lý hộp thư TempMail.Plus trên Windows.
- Thêm hộp thư đang có (địa chỉ nhận thư + địa chỉ bí mật + PIN).
- Tạo một lô với tiến độ thật (vd 12/20); dừng giữa chừng vẫn giữ các hộp đã tạo.
- Tự chuyển sang tạo thủ công trên TempMail.Plus khi trang yêu cầu captcha.
- "Đã áp dụng" chỉ hiện khi đọc lại được cài đặt từ TempMail.Plus.
- PIN và địa chỉ bí mật lưu trong Windows Credential Manager, ẩn trên màn hình.
