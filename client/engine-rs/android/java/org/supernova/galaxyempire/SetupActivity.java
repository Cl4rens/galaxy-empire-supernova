package org.supernova.galaxyempire;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * The app's first screen. tools/patch_apk.py --64bit put the game files from the player's own
 * APK and the server address into this app; then this screen starts the game right away. Back
 * from the game leads here, to change the server address or to see why the game stopped.
 *
 * The engine (android/src/lib.rs) reads its settings from the app's folder: apk_path.txt (this
 * app's own APK, with the game files), server.txt (an address entered here) and error.txt.
 */
public class SetupActivity extends Activity {
    private static final String GAME_ASSET = "tap4fun.zip";
    private static final String BAKED_SERVER_ASSET = "supernova/server.txt";
    private static final String APK_PATH = "apk_path.txt";
    private static final String SERVER = "server.txt";
    private static final String ERROR = "error.txt";
    private static final String DEFAULT_PORT = "8000";

    private TextView status;
    private TextView error;
    private EditText server;
    private Button play;
    private boolean patched;
    private String bakedServer;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        patched = hasAsset(GAME_ASSET);
        bakedServer = asset(BAKED_SERVER_ASSET);
        write(APK_PATH, getApplicationInfo().sourceDir);
        setContentView(layout());
        String own = read(SERVER);
        server.setText(own.isEmpty() ? bakedServer : own);
        if (state == null && patched && !file(ERROR).exists()) {
            startGame();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        String problem = read(ERROR);
        error.setText(problem.isEmpty() ? "" : "The game stopped: " + problem);
    }

    private View layout() {
        int pad = dp(24);
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(pad, pad, pad, pad);
        column.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView title = text("Galaxy Empire · Supernova", 24);
        title.setTextColor(Color.WHITE);
        column.addView(title);
        status = text(patched
                ? "Ready to play. Change the server address here if it moved."
                : "This app has no game files yet. Make it on your computer from your own "
                + "Galaxy Empire APK: python tools/patch_apk.py <your apk> --64bit --server <address>", 15);
        column.addView(status);

        column.addView(text("Server address", 15));
        server = new EditText(this);
        server.setHint("192.168.1.20:8000");
        server.setSingleLine(true);
        server.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        server.setTextColor(Color.WHITE);
        server.setHintTextColor(Color.GRAY);
        column.addView(server);

        play = button("Play");
        play.setEnabled(patched);
        play.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String address = normalize(server.getText().toString());
                if (address.isEmpty() || address.equals(normalize(bakedServer))) {
                    file(SERVER).delete();
                } else {
                    write(SERVER, address);
                }
                file(ERROR).delete();
                error.setText("");
                startGame();
            }
        });
        column.addView(play);

        error = text("", 14);
        error.setTextColor(Color.rgb(255, 120, 120));
        column.addView(error);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(8, 12, 28));
        scroll.addView(column);
        return scroll;
    }

    private void startGame() {
        startActivity(new Intent(this, android.app.NativeActivity.class));
    }

    /** "192.168.1.20" -> "http://192.168.1.20:8000" */
    static String normalize(String address) {
        String s = address.trim();
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        if (s.isEmpty()) {
            return "";
        }
        if (!s.contains("://")) {
            s = "http://" + s;
        }
        String host = s.substring(s.indexOf("://") + 3);
        if (!host.contains(":")) {
            s = s + ":" + DEFAULT_PORT;
        }
        return s;
    }

    private boolean hasAsset(String name) {
        try (InputStream in = getAssets().open(name)) {
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private String asset(String name) {
        try (InputStream in = getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            for (int n; (n = in.read(buffer)) > 0; ) {
                out.write(buffer, 0, n);
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8).trim();
        } catch (Exception e) {
            return "";
        }
    }

    private File folder() {
        File dir = getExternalFilesDir(null);
        if (dir == null) {
            dir = getFilesDir();
        }
        dir.mkdirs();
        return dir;
    }

    private File file(String name) {
        return new File(folder(), name);
    }

    private String read(String name) {
        File f = file(name);
        if (!f.exists()) {
            return "";
        }
        try (InputStream in = new FileInputStream(f)) {
            byte[] data = new byte[(int) Math.min(f.length(), 4096)];
            int n = in.read(data);
            return n > 0 ? new String(data, 0, n, StandardCharsets.UTF_8).trim() : "";
        } catch (Exception e) {
            return "";
        }
    }

    private void write(String name, String text) {
        try (OutputStream out = new FileOutputStream(file(name))) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            if (error != null) {
                error.setText("Could not save " + name + ": " + e.getMessage());
            }
        }
    }

    private TextView text(String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(Color.rgb(190, 205, 230));
        t.setPadding(0, dp(10), 0, dp(10));
        return t;
    }

    private Button button(String s) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        return b;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
