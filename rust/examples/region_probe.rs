use std::cell::RefCell;
use std::rc::Rc;

fn main() {
    std::env::remove_var("CARGO_MANIFEST_DIR");
    abyssal::skin::hidpi::set_scale(1.6);
    abyssal::ui::fonts::ensure_user_fonts();
    let l = abyssal::layout::resolve(900.0, 700.0);
    let history = Rc::new(RefCell::new(abyssal::telemetry::History::new(5.0)));
    let m = abyssal::ui::console::ConsoleModel::new(
        abyssal::species::by_index(0), 0, history.clone(), false);
    let tel = abyssal::signals::Telemetry::new();
    let mut renderer = abyssal::ui::console::Renderer::new();
    let under = renderer.layer_under(&l, &m);
    let over = renderer.layer_over(&l, &m, &tel);
    let regions = renderer.regions(&l, &m, &tel, 60.0, 16.6, under.as_ref(), over.as_ref());
    println!("under: {:?} over: {:?}", under.as_ref().map(|x| (x.surface.width(), x.surface.height())), over.as_ref().map(|x| (x.surface.width(), x.surface.height())));
    println!("{} regions:", regions.len());
    for r in &regions {
        println!("  rect {:?}", r.rect);
        let path = format!("/tmp/region_{}_{}", r.rect.x as i32, r.rect.y as i32);
        let surf = r.layer.surface.clone();
        let mut png = std::fs::File::create(format!("{path}.png")).unwrap();
        let _ = surf.write_to_png(&mut png);
        println!("  -> saved {path}.png ({}x{})", r.layer.surface.width(), r.layer.surface.height());
    }
}
