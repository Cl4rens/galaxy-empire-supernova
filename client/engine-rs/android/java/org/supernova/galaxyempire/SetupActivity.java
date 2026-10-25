package org.supernova.galaxyempire;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.zip.ZipFile;

/**
 * The app's first screen: the player picks their own Galaxy Empire APK (the app ships nothing
 * from the game) and enters the server address. Both go to the folder the engine reads
 * (android/src/lib.rs): galaxy-empire.apk and server.txt. Once both are there, the app starts
 * the game right away; back from the game leads here again.
 */
public class SetupActivity extends Activity {
    private static final int PICK_APK = 1;
    private static final String APK = "galaxy-empire.apk";
    private static final String SERVER = "server.txt";
    private static final String ERROR = "error.txt";
    private static final String DEFAULT_PORT = "8000";

    private TextView status;
    private TextView error;
    private EditText server;
    private Button play;
    private Button pick;
    private boolean copying;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(layout());
        server.setText(read(SERVER));
        if (state == null && ready() && !file(ERROR).exists()) {
            startGame();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
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
        column.addView(text("This app plays your own copy of Galaxy Empire 1.9.36. "
                + "Choose the game's APK file once; it stays in the app.", 15));

        pick = button("Choose Galaxy Empire APK");
        pick.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                startActivityForResult(intent, PICK_APK);
            }
        });
        column.addView(pick);
        status = text("", 15);
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
        play.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                write(SERVER, normalize(server.getText().toString()));
                file(ERROR).delete();
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

    private void refresh() {
        File apk = file(APK);
        if (copying) {
            status.setText("Copying the game file…");
        } else if (apk.exists()) {
            status.setText("Game file ready (" + apk.length() / (1024 * 1024) + " MB).");
        } else {
            status.setText("No game file chosen yet.");
        }
        pick.setEnabled(!copying);
        play.setEnabled(!copying && apk.exists());
        String problem = read(ERROR);
        error.setText(problem.isEmpty() ? "" : "The game stopped: " + problem);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request != PICK_APK || result != RESULT_OK || data == null || data.getData() == null) {
            return;
        }
        final Uri uri = data.getData();
        copying = true;
        refresh();
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String problem = copy(uri);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        copying = false;
                        refresh();
                        if (problem != null) {
                            error.setText(problem);
                        }
                    }
                });
            }
        }).start();
    }

    /** Copies the chosen file into the app and checks that it is the game; null when it is. */
    private String copy(Uri uri) {
        File partial = file(APK + ".part");
        try (InputStream in = getContentResolver().openInputStream(uri);
             OutputStream out = new FileOutputStream(partial)) {
            byte[] buffer = new byte[1 << 16];
            for (int n; (n = in.read(buffer)) > 0; ) {
                out.write(buffer, 0, n);
            }
        } catch (Exception e) {
            partial.delete();
            return "Could not read the file: " + e.getMessage();
        }
        try (ZipFile zip = new ZipFile(partial)) {
            if (zip.getEntry("assets/tap4fun.zip") == null || zip.getEntry("lib/armeabi-v7a/libgalaxyempire.so") == null) {
                partial.delete();
                return "That is not the Galaxy Empire APK.";
            }
        } catch (Exception e) {
            partial.delete();
            return "That is not an APK file.";
        }
        File[] old = folder().listFiles();
        if (old != null) {
            for (File f : old) {
                if (f.getName().toLowerCase().endsWith(".apk")) {
                    f.delete();
                }
            }
        }
        file(ERROR).delete();
        return partial.renameTo(file(APK)) ? null : "Could not store the file.";
    }

    private void startGame() {
        Intent intent = new Intent(this, android.app.NativeActivity.class);
        startActivity(intent);
    }

    private boolean ready() {
        return file(APK).exists() && !read(SERVER).isEmpty();
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
            error.setText("Could not save " + name + ": " + e.getMessage());
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
