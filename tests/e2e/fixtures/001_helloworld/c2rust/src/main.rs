extern "C" {
    fn printf(__format: *const core::ffi::c_char, ...) -> core::ffi::c_int;
}
unsafe fn main_0() -> core::ffi::c_int {
    printf(b"Hello World!\n\0" as *const u8 as *const core::ffi::c_char);
    return 0 as core::ffi::c_int;
}
pub fn main() {
    unsafe { ::std::process::exit(main_0() as i32) }
}
