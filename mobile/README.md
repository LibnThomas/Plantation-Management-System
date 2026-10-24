# Plantation MS – phone app (Android / iPhone), fully offline

The phone app contains the whole Plantation Management System. Its database lives inside the phone, so it
needs no server, no Wi-Fi and no internet.

- **First start:** the app creates its database with the standard drop-down lists, then shows the login.
  This takes about a minute.
- **First login:** username `admin`, password `admin1234`. Change the password straight away (user menu >
  Change password).
- **Updates:** install the new version over the old one. Your data stays, and the database is updated
  automatically.
- **Separate phones:** each phone has its own data, with no link to the server or to other phones.
  Demo mode works as usual.
- **Files:** on Android, PDF, Excel (CSV) and backup files are saved to **Downloads/PlantationMS** and
  opened with a suitable app.
- **Backups:** uninstalling the app deletes its data. Use **App Settings > Security > Download backup**
  regularly and keep the file somewhere safe.

## Building the apps

Everything is in this `mobile` folder. The Django code is the same one the server uses: `make_bundle.py`
packs it into the app on every build. Secrets (`.env`), databases and tests are never packed.

### Android (.apk) – on your Mac

1. Install Python 3.12 from https://www.python.org/downloads/macos/ (3.10–3.13 also work).
2. In Terminal:
   ```
   cd ~/Documents/Personal/plantation_ms/mobile
   ./build_android.sh
   ```
   The first build downloads Java, the Android SDK and the packages, about 2 GB. It takes 10–20 minutes
   and asks you to accept Google's licence: type `y` when asked. Later builds take 1–3 minutes.
3. The app is saved as `mobile/dist/plantation-ms.apk`. Send it to the phone (WhatsApp, USB or Drive),
   open it, and allow "Install unknown apps".

To build and start it straight away on a phone connected by USB, with USB debugging on, run
`./build_android.sh run`.

### iPhone – on your Mac (needs Xcode from the App Store)

| Command | What you get | Apple ID? |
|---|---|---|
| `./build_ios.sh` | The app runs in the **iPhone Simulator** on the Mac | No |
| `./build_ios.sh ipa` | `mobile/dist/plantation-ms-unsigned.ipa`, installed with **Sideloadly** or **AltStore** | Free Apple ID (re-install every 7 days) |
| `./build_ios.sh xcode` | Opens Xcode. Set Team = your Apple ID, choose your iPhone, press Run | Free Apple ID (7 days) |

An iPhone only accepts apps signed by Apple; a completely unsigned app can't be installed. For a year-long
install, or TestFlight for other people, use a paid Apple Developer account ($99/year).

### Without building on your Mac: GitHub

Copy `mobile/github-workflow-mobile-apps.yml` to `.github/workflows/mobile-apps.yml` in the project. Push
the project to a **private** GitHub repository, then open Actions > **Phone apps** > Run workflow.
After about 20 minutes, download `plantation-ms.apk` and `plantation-ms-unsigned.ipa` from the run's
Artifacts.

### Try the phone version on the computer

```
python3 make_bundle.py
PYTHONPATH=src python3 -m pms_mobile.launcher --data /tmp/pms-phone
```

Then open the address it prints.

## Known limits on the phone

- **Uploads:** choosing a file, such as the estate logo or a labels file, works on iPhone. On Android it
  doesn't yet, so set the logo on the server version.
- **PDF letters:** Malayalam, Hindi and Tamil text in PDFs may show letters less neatly joined than on the
  server. The shaping library has no phone version.
- **Restoring a backup on the phone:** not available yet.
