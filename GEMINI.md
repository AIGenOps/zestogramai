# CI/CD Verification Rule

Whenever you modify code or implement new features, you MUST ALWAYS perform a verification check before concluding your work. 

To satisfy this requirement:
1. Rebuild the application using the appropriate command (e.g., `docker compose up -d --build bot`).
2. Check the logs using `docker compose logs bot --tail 50` to ensure that the container started successfully, there are no syntax errors, and the application is running cleanly.
3. If errors are found, fix them and repeat the process until the application is fully functional.
4. Only report back to the user once you have verified the logs are clean and the application is successfully deployed.
