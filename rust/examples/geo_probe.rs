fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    let l = abyssal::layout::resolve(900.0, 700.0);
    println!("controls rect: {:?}", l.controls);
    let (geo, mode) = abyssal::ui::console::control_geometry(&l);
    println!(
        "RS geo: key_x={:.2} key_y={:.2} key_w={:.2} key_h={:.2} pitch={:.2} n={}",
        geo.key_x, geo.key_y, geo.key_w, geo.key_h, geo.pitch, geo.n
    );
    println!("RS key_rect(0): {:?}", geo.key_rect(0));
    println!("RS key_rect(4): {:?}", geo.key_rect(4));
    println!("RS aux_l: {:?}", geo.aux_l);
    println!("RS aux_r: {:?}", mode);
}
