//! Shows the game in a window: stacks the screens' textures (bottom first) on the window's
//! surface, in a 640x960 frame scaled to fit, with black bars around it.

use std::sync::Arc;

use anyhow::{Result, anyhow};
use ruffle_render_wgpu::descriptors::Descriptors;
use ruffle_render_wgpu::wgpu;

use crate::screens::{HEIGHT, WIDTH};

const SHADER: &str = r#"
struct Out {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs(@builtin(vertex_index) i: u32) -> Out {
    // one triangle that covers the viewport
    var corners = array<vec2<f32>, 3>(vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    let p = corners[i];
    var out: Out;
    out.position = vec4(p, 0.0, 1.0);
    out.uv = vec2((p.x + 1.0) * 0.5, (1.0 - p.y) * 0.5);
    return out;
}

@group(0) @binding(0) var layer: texture_2d<f32>;
@group(0) @binding(1) var layer_sampler: sampler;

@fragment
fn fs(in: Out) -> @location(0) vec4<f32> {
    return textureSample(layer, layer_sampler, in.uv);
}
"#;

pub struct Compositor {
    descriptors: Arc<Descriptors>,
    surface: wgpu::Surface<'static>,
    config: wgpu::SurfaceConfiguration,
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
}

/// Where the 640x960 frame sits in a window of this size: (x, y, width, height).
pub fn frame(window_width: u32, window_height: u32) -> (f32, f32, f32, f32) {
    let (ww, wh) = (window_width.max(1) as f32, window_height.max(1) as f32);
    let scale = (ww / WIDTH as f32).min(wh / HEIGHT as f32);
    let (w, h) = (WIDTH as f32 * scale, HEIGHT as f32 * scale);
    ((ww - w) / 2.0, (wh - h) / 2.0, w, h)
}

/// A point in the window as a point in the game's 640x960 coordinates.
pub fn to_game(window_width: u32, window_height: u32, x: f64, y: f64) -> (f64, f64) {
    let (fx, fy, fw, fh) = frame(window_width, window_height);
    (
        (x - fx as f64) * WIDTH as f64 / fw as f64,
        (y - fy as f64) * HEIGHT as f64 / fh as f64,
    )
}

impl Compositor {
    pub fn new(descriptors: Arc<Descriptors>, surface: wgpu::Surface<'static>, width: u32, height: u32) -> Result<Self> {
        let device = &descriptors.device;
        let caps = surface.get_capabilities(&descriptors.adapter);
        // the screens hold plain (not sRGB) colors, as Flash had them
        let format = caps
            .formats
            .iter()
            .copied()
            .find(|f| !f.is_srgb())
            .or_else(|| caps.formats.first().copied())
            .ok_or_else(|| anyhow!("the window surface supports no format"))?;
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            width: width.max(1),
            height: height.max(1),
            present_mode: wgpu::PresentMode::AutoVsync,
            desired_maximum_frame_latency: 2,
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            color_space: wgpu::SurfaceColorSpace::Auto,
        };
        surface.configure(device, &config);
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("compositor"),
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("layer"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("compositor"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("compositor"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs"),
                buffers: &[],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs"),
                targets: &[Some(wgpu::ColorTargetState {
                    format,
                    // Ruffle's textures hold premultiplied alpha
                    blend: Some(wgpu::BlendState::PREMULTIPLIED_ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("layer"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        Ok(Self { descriptors, surface, config, pipeline, layout, sampler })
    }

    pub fn resize(&mut self, width: u32, height: u32) {
        self.config.width = width.max(1);
        self.config.height = height.max(1);
        self.surface.configure(&self.descriptors.device, &self.config);
    }

    pub fn size(&self) -> (u32, u32) {
        (self.config.width, self.config.height)
    }

    /// Show the layers, bottom first.
    pub fn draw(&mut self, layers: &[wgpu::Texture]) {
        let device = &self.descriptors.device;
        let output = match self.surface.get_current_texture() {
            wgpu::CurrentSurfaceTexture::Success(t) | wgpu::CurrentSurfaceTexture::Suboptimal(t) => t,
            state => {
                tracing::debug!("no surface texture: {state:?}");
                self.surface.configure(device, &self.config);
                return;
            }
        };
        let view = output.texture.create_view(&Default::default());
        let groups: Vec<wgpu::BindGroup> = layers
            .iter()
            .map(|texture| {
                let layer_view = texture.create_view(&Default::default());
                device.create_bind_group(&wgpu::BindGroupDescriptor {
                    label: Some("layer"),
                    layout: &self.layout,
                    entries: &[
                        wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::TextureView(&layer_view) },
                        wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::Sampler(&self.sampler) },
                    ],
                })
            })
            .collect();
        let mut encoder = device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("compositor"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations { load: wgpu::LoadOp::Clear(wgpu::Color::BLACK), store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            let (x, y, w, h) = frame(self.config.width, self.config.height);
            pass.set_viewport(x, y, w, h, 0.0, 1.0);
            pass.set_pipeline(&self.pipeline);
            for group in &groups {
                pass.set_bind_group(0, group, &[]);
                pass.draw(0..3, 0..1);
            }
        }
        self.descriptors.queue.submit([encoder.finish()]);
        self.descriptors.queue.present(output);
    }
}
