//! Play one converted screen headless and save a picture of it.
//!
//!     screen-test <screen.swf> <out.png> [frames] [function args...]
//!
//! After `frames` frames it calls `function(args)` on `_root` (like the game's
//! `InvokeASCallback`), runs a few more frames and prints every fscommand the screen sent.

use std::sync::{Arc, Mutex};

use anyhow::{Result, anyhow};
use ruffle_core::PlayerBuilder;
use ruffle_core::external::{FsCommandProvider, Value};
use ruffle_core::limits::ExecutionLimit;
use ruffle_core::tag_utils::movie_from_path;
use ruffle_render_wgpu::backend::{WgpuRenderBackend, create_wgpu_instance, request_adapter_and_device};
use ruffle_render_wgpu::descriptors::Descriptors;
use ruffle_render_wgpu::target::TextureTarget;
use ruffle_render_wgpu::wgpu;

struct Commands(Arc<Mutex<Vec<(String, String)>>>);

impl FsCommandProvider for Commands {
    fn on_fs_command(&self, command: &str, args: &str) -> bool {
        self.0.lock().unwrap().push((command.to_string(), args.to_string()));
        true
    }
}

fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter("warn").init();
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        return Err(anyhow!("usage: screen-test <screen.swf> <out.png> [frames] [function args...]"));
    }
    let frames: u32 = args.get(3).map(|f| f.parse()).transpose()?.unwrap_or(10);

    let instance = create_wgpu_instance(wgpu::Backends::all(), wgpu::BackendOptions::default(), None);
    let (adapter, device, queue) = futures::executor::block_on(request_adapter_and_device(
        wgpu::Backends::all(),
        &instance,
        None,
        wgpu::PowerPreference::HighPerformance,
    ))
    .map_err(|e| anyhow!(e.to_string()))?;
    let descriptors = Arc::new(Descriptors::new(instance, adapter, device, queue));

    let movie = movie_from_path(&args[1], None).map_err(|e| anyhow!(e.to_string()))?;
    let (width, height) = (movie.width().to_pixels() as u32, movie.height().to_pixels() as u32);
    let target = TextureTarget::new(&descriptors.device, (width, height)).map_err(|e| anyhow!(e.to_string()))?;
    let commands = Arc::new(Mutex::new(Vec::new()));
    let player = PlayerBuilder::new()
        .with_renderer(WgpuRenderBackend::new(descriptors.clone(), target).map_err(|e| anyhow!(e.to_string()))?)
        .with_fs_commands(Box::new(Commands(commands.clone())))
        .with_movie(movie)
        .with_viewport_dimensions(width, height, 1.0)
        .build();

    let run = |n: u32| {
        for _ in 0..n {
            let mut p = player.lock().unwrap();
            p.preload(&mut ExecutionLimit::none());
            p.run_frame();
        }
    };
    run(frames);
    if let Some(function) = args.get(4) {
        let values: Vec<Value> = args[5..].iter().map(|a| Value::String(a.clone())).collect();
        let result = player.lock().unwrap().call_avm1_function("_root", function, values);
        println!("{function}(...) = {result:?}");
        run(5);
    }
    for (command, arg) in commands.lock().unwrap().iter() {
        println!("fscommand {command} {arg}");
    }

    let mut p = player.lock().unwrap();
    p.render();
    let renderer = <dyn std::any::Any>::downcast_mut::<WgpuRenderBackend<TextureTarget>>(p.renderer_mut())
        .ok_or_else(|| anyhow!("not a wgpu texture renderer"))?;
    let image = renderer.capture_frame().ok_or_else(|| anyhow!("no frame"))?;
    image.save(&args[2])?;
    println!("saved {} ({width}x{height})", args[2]);
    Ok(())
}
