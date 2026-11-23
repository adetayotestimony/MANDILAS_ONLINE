# MANDILAS ONLINE backend
# MANDILAS ONLINE

1. pip install -r requirements.txt
2. Copy .env.example to .env and fill it in (never share .env)
3. python app.py  ->  http://127.0.0.1:5000

Pages: / (shop)  /product/<id>  /account  /track  /admin
Emails: until Gmail is set up, every email is saved as a web page in email_previews/ so you can open it and see the design.
Paystack: set the webhook URL to https://YOUR-SITE/api/paystack/webhook
Google: after going live, add your site in Google Search Console and submit https://YOUR-SITE/sitemap.xml
Back up mandilas.db and the uploads/ folder regularly.