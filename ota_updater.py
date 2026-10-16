import urequests
import os
import machine
import time

class OTAUpdater:
    def __init__(self, repo_url, current_version, wdt=None):
        self.repo_url = repo_url.rstrip('/')
        self.current_version = current_version
        self.wdt = wdt

    def _feed_wdt(self):
        if self.wdt:
            self.wdt.feed()

    def check_for_updates(self):
        print("Checking GitHub for updates...")
        self._feed_wdt()
        try:
            response = urequests.get(f"{self.repo_url}/version.json")
            if response.status_code == 200:
                remote_data = response.json()
                remote_version = remote_data.get("version")
                response.close()

                print("Current:", self.current_version, "| Remote:", remote_version)
                return (remote_version != self.current_version), remote_version
            else:
                response.close()
                print("Version check failed. Status:", response.status_code)
                return False, self.current_version
        except Exception as e:
            print("Error checking for updates:", e)
            return False, self.current_version

    def safe_download_and_install(self, filenames=["main.py"]):
        for filename in filenames:
            self._feed_wdt()
            temp_filename = filename + ".tmp"
            backup_filename = filename + ".bak"
            file_url = f"{self.repo_url}/{filename}"
            
            print("Downloading", filename, "from", file_url)
            try:
                response = urequests.get(file_url)
                if response.status_code == 200:
                    code = response.text
                    response.close()

                    # Write new code to temporary file first (atomic safety)
                    with open(temp_filename, "w") as f:
                        f.write(code)

                    # Manage backups
                    try:
                        os.remove(backup_filename)
                    except OSError:
                        pass

                    try:
                        os.rename(filename, backup_filename)
                    except OSError:
                        pass

                    # Atomic swap
                    os.rename(temp_filename, filename)
                    print("Successfully updated", filename)
                else:
                    response.close()
                    print("Failed to download", filename, "Status:", response.status_code)
                    return False
            except Exception as e:
                print("Error during update of", filename, ":", e)
                return False

        print("All files updated safely. Rebooting in 3 seconds...")
        for _ in range(3):
            self._feed_wdt()
            time.sleep(1)
        machine.reset()
      
