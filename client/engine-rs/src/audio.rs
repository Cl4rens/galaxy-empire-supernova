//! Music and sound effects: what the game plays through `ext.audio` and the Flash command
//! PLAYSFX (button clicks), from its `sound/` files.

use std::io::Cursor;

use rodio::{Decoder, DeviceSinkBuilder, MixerDeviceSink, Player, Source};

pub struct Audio {
    output: MixerDeviceSink,
    /// the background music, looped
    music: Player,
    sound_volume: f32,
    paused: bool,
}

impl Audio {
    /// The system's sound output; without one the game plays silently.
    pub fn open() -> Option<Self> {
        let mut output = DeviceSinkBuilder::open_default_sink()
            .map_err(|e| tracing::warn!("no sound output: {e}"))
            .ok()?;
        output.log_on_drop(false);
        let music = Player::connect_new(output.mixer());
        Some(Self { output, music, sound_volume: 1.0, paused: false })
    }

    pub fn play_music(&mut self, data: Vec<u8>) {
        self.music.clear();
        match Decoder::new_looped(Cursor::new(data)) {
            Ok(music) => {
                self.music.append(music);
                if !self.paused {
                    self.music.play();
                }
            }
            Err(e) => tracing::warn!("music: {e}"),
        }
    }

    pub fn stop_music(&mut self) {
        self.music.clear();
    }

    pub fn play_effect(&self, data: Vec<u8>) {
        if self.paused || self.sound_volume <= 0.0 {
            return;
        }
        match Decoder::try_from(Cursor::new(data)) {
            Ok(effect) => self.output.mixer().add(effect.amplify(self.sound_volume)),
            Err(e) => tracing::warn!("sound: {e}"),
        }
    }

    /// The game's settings: 0 (off) to 1
    pub fn set_music_volume(&mut self, volume: f32) {
        self.music.set_volume(volume.clamp(0.0, 1.0));
    }

    pub fn set_sound_volume(&mut self, volume: f32) {
        self.sound_volume = volume.clamp(0.0, 1.0);
    }

    /// Quiet while the app is in the background.
    pub fn pause(&mut self, paused: bool) {
        self.paused = paused;
        if paused {
            self.music.pause();
        } else if !self.music.empty() {
            self.music.play();
        }
    }
}
