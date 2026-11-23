"""Branded HTML emails for MANDILAS ONLINE. Table layout + inline CSS so Gmail/Outlook/phones all render it."""
import html as _h
import os

BRAND, INK, CREAM = "#8b0d1c", "#17181a", "#efebe2"
FONT = "Arial,Helvetica,sans-serif"
TIKTOK = "https://www.tiktok.com/@mandilas.online"
WHATSAPP = "https://wa.me/message/GYV4F2ZKHZ4LP1"


def site():
    return os.environ.get("SITE_URL", "http://127.0.0.1:5000").rstrip("/")


def e(s):
    return _h.escape(str(s if s is not None else ""))


def money(n):
    return "&#8358;{:,.0f}".format(n or 0)


def h1(text):
    return '<h1 style="margin:0 0 12px;font-family:%s;font-size:26px;line-height:1.2;color:%s;text-transform:uppercase;letter-spacing:.3px">%s</h1>' % (FONT, INK, e(text))


def p(text, muted=False):
    color = "#6b6a64" if muted else INK
    body = e(text).replace("\n", "<br>")
    return '<p style="margin:0 0 16px;font-family:%s;font-size:15px;line-height:1.6;color:%s">%s</p>' % (FONT, color, body)


def button(url, label):
    return ('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:22px 0"><tr><td bgcolor="%s" style="background:%s">'
            '<a href="%s" style="display:inline-block;padding:14px 28px;font-family:%s;font-size:13px;font-weight:bold;letter-spacing:1.5px;color:#ffffff;text-decoration:none;text-transform:uppercase">%s</a>'
            '</td></tr></table>') % (BRAND, BRAND, e(url), FONT, e(label))


def code_box(code):
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:18px 0"><tr><td align="center" bgcolor="#0b0b0a" style="background:#0b0b0a;padding:24px">'
            '<span style="font-family:\'Courier New\',monospace;font-size:40px;letter-spacing:12px;font-weight:bold;color:#ffffff">%s</span>'
            '</td></tr><tr><td style="background:%s;height:4px;font-size:0;line-height:0">&nbsp;</td></tr></table>') % (e(code), BRAND)


def info_box(rows):
    cells = "".join('<tr><td style="padding:5px 0;font-family:%s;font-size:12px;color:#6b6a64;text-transform:uppercase;letter-spacing:1px;width:34%%;vertical-align:top">%s</td>'
                    '<td style="padding:5px 0;font-family:%s;font-size:14px;color:%s">%s</td></tr>' % (FONT, e(k), FONT, INK, e(v).replace("\n", "<br>")) for k, v in rows if v)
    return '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:#f6f3ec;margin:6px 0 18px;padding:14px"><tr><td style="padding:12px 16px"><table width="100%%" cellpadding="0" cellspacing="0">%s</table></td></tr></table>' % cells


def items(lines, imgs=None):
    imgs, out = imgs or {}, ""
    for l in lines:
        cid = imgs.get(l.get("id"))
        pic = ('<img src="cid:%s" width="64" height="64" alt="" style="display:block;object-fit:cover;border:0">' % cid) if cid else ""
        out += ('<tr><td width="76" style="padding:10px 0;border-bottom:1px solid #e6e1d4">%s</td>'
                '<td style="padding:10px 8px;border-bottom:1px solid #e6e1d4;font-family:%s;font-size:14px;color:%s"><b>%s</b><br><span style="color:#6b6a64;font-size:12px">Size EU %s &middot; Qty %s</span></td>'
                '<td align="right" style="padding:10px 0;border-bottom:1px solid #e6e1d4;font-family:%s;font-size:14px;color:%s;white-space:nowrap">%s</td></tr>'
                % (pic, FONT, INK, e(l["name"]), e(l["size"]), l["qty"], FONT, INK, money(l["price"] * l["qty"])))
    return '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:8px 0">%s</table>' % out


def totals(rows):
    out = "".join('<tr><td style="padding:4px 0;font-family:%s;font-size:%s;color:%s;%s">%s</td><td align="right" style="padding:4px 0;font-family:%s;font-size:%s;color:%s;%s">%s</td></tr>'
                  % (FONT, "17px" if b else "14px", INK if b else "#6b6a64", "font-weight:bold;" if b else "", e(k),
                     FONT, "17px" if b else "14px", BRAND if b else INK, "font-weight:bold;" if b else "", v) for k, v, b in rows)
    return '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:6px 0 14px">%s</table>' % out


STEPS = [("paid", "Paid"), ("processing", "Preparing"), ("dispatched", "Dispatched"), ("out_for_delivery", "On the way"), ("delivered", "Delivered")]


def progress(status):
    reached = [s for s, _ in STEPS].index(status) if status in dict(STEPS) else -1
    cells = ""
    for i, (_, label) in enumerate(STEPS):
        on = i <= reached
        cells += ('<td width="20%%" style="padding:0 2px"><div style="height:6px;background:%s;font-size:0;line-height:0">&nbsp;</div>'
                  '<div style="font-family:%s;font-size:10px;letter-spacing:.5px;text-transform:uppercase;padding-top:6px;color:%s;text-align:center">%s</div></td>'
                  % (BRAND if on else "#d9d4c6", FONT, INK if on else "#9a988f", label))
    return '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:18px 0 22px"><tr>%s</tr></table>' % cells


def product_card(name, price, cid, url, label="View shoe", sub=""):
    pic = ('<img src="cid:%s" width="540" alt="%s" style="display:block;width:100%%;max-width:540px;height:auto;border:0">' % (cid, e(name))) if cid else ""
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:10px 0"><tr><td>%s</td></tr>'
            '<tr><td style="padding:14px 0 0;font-family:%s;font-size:20px;color:%s"><b>%s</b></td></tr>'
            '<tr><td style="font-family:%s;font-size:13px;color:#6b6a64;padding-top:2px">%s</td></tr>'
            '<tr><td style="font-family:%s;font-size:18px;color:%s;padding-top:6px"><b>%s</b></td></tr><tr><td>%s</td></tr></table>'
            % (pic, FONT, INK, e(name), FONT, e(sub), FONT, BRAND, money(price), button(url, label)))


def layout(body, preheader="", unsub_url=None):
    s = site()
    foot_links = " &nbsp;&middot;&nbsp; ".join('<a href="%s" style="color:#d8d5ca;text-decoration:none">%s</a>' % (u, t) for u, t in
                                            [(s, "Shop"), (s + "/track", "Track order"), (TIKTOK, "TikTok"), (WHATSAPP, "WhatsApp")])
    unsub = ('<br><br><a href="%s" style="color:#a9a79e">Unsubscribe from news and offers</a>' % e(unsub_url)) if unsub_url else ""
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="margin:0;padding:0;background:%s"><span style="display:none;max-height:0;overflow:hidden;opacity:0;color:%s">%s</span>'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:%s"><tr><td align="center" style="padding:24px 10px">'
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%%;max-width:600px;background:#ffffff">'
            '<tr><td align="center" bgcolor="#0b0b0a" style="background:#0b0b0a;padding:30px 20px"><a href="%s"><img src="cid:logo" width="200" alt="MANDILAS ONLINE" style="display:block;margin:0 auto;height:auto;border:0"></a></td></tr>'
            '<tr><td style="background:%s;height:5px;font-size:0;line-height:0">&nbsp;</td></tr>'
            '<tr><td style="padding:34px 30px 24px">%s</td></tr>'
            '<tr><td align="center" bgcolor="#0b0b0a" style="background:#0b0b0a;padding:24px 20px;font-family:%s;font-size:12px;line-height:1.7;color:#a9a79e">'
            '<b style="color:#ffffff;letter-spacing:2px">THE HOUSE OF FEET</b><br>%s<br>mandilasonline@gmail.com%s</td></tr></table></td></tr></table></body></html>'
            % (CREAM, CREAM, e(preheader), CREAM, s, BRAND, body, FONT, foot_links, unsub))


# ------------------------------------------------------------------ the emails
def otp_email(code):
    return layout(h1("Your login code") + p("Use this code to sign in to MANDILAS ONLINE:") + code_box(code)
                  + p("It expires in 10 minutes. If you didn't ask for it, you can ignore this email.", True), "Your login code is " + code)


def welcome_email(name):
    return layout(h1("Welcome, %s" % name) + p("Your account is ready. Browse the catalog, save your favourite pairs and track every order from one place.")
                  + button(site() + "/#shop", "Start shopping"), "Your MANDILAS ONLINE account is ready")


def order_confirmation(o, lines, imgs, discount_row, track_url):
    t = [("Subtotal", money(o["subtotal"]), False)]
    if o["discount"]:
        t.append(("Discount (%s)" % e(o["discount_code"]), "&minus;" + money(o["discount"]), False))
    t.append(("Total paid", money(o["total"]), True))
    body = (h1("Thank you, %s" % o["name"].split(" ")[0]) + p("Your payment was received and your order is confirmed.")
            + info_box([("Order", o["reference"])]) + items(lines, imgs) + totals(t)
            + info_box([("Delivering to", "%s\n%s" % (o["address"], o["city"])), ("Phone", o["phone"])]) + button(track_url, "Track my order"))
    return layout(body, "Order %s confirmed" % o["reference"])


def owner_order(o, lines, imgs, stock_warning, admin_url):
    body = (h1("New paid order") + (p("CHECK STOCK: a size ran short on this order.") if stock_warning else "")
            + info_box([("Order", o["reference"]), ("Total", re_plain(money(o["total"]))), ("Customer", o["name"]), ("Email", o["email"]), ("Phone", o["phone"]),
                        ("Address", "%s\n%s" % (o["address"], o["city"]))]) + items(lines, imgs) + button(admin_url, "Open dashboard"))
    return layout(body, "New order %s" % o["reference"])


def re_plain(s):
    return _h.unescape(s)


def status_email(o, headline, line, track_url, imgs=None, lines=None):
    extra = []
    if o.get("rider_name") or o.get("rider_phone"):
        extra.append(("Rider", ("%s %s" % (o.get("rider_name", ""), o.get("rider_phone", ""))).strip()))
    if o.get("eta"):
        extra.append(("Expected", o["eta"]))
    if o.get("tracking_note"):
        extra.append(("Note", o["tracking_note"]))
    body = h1(headline) + p("Hi %s, %s" % (o["name"].split(" ")[0], line)) + (progress(o["status"]) if o["status"] in dict(STEPS) else "")
    body += info_box([("Order", o["reference"])] + extra) + (items(lines, imgs) if lines else "") + button(track_url, "Track my order")
    return layout(body, headline)


def refund_email(name, amount, ref):
    return layout(h1("Refund on its way") + p("Hi %s, we've started a refund of %s for order %s." % (name, re_plain(money(amount)), ref))
                  + p("Your bank can take a few days to show it.", True), "Your refund has started")


def ticket_reply_email(kind, status, reply):
    return layout(h1("Update on your %s request" % kind) + info_box([("Status", status)]) + p(reply) + button(site() + "/account#support", "View my requests"), "We replied to your request")


def abandoned_email(name, lines, imgs, subtotal):
    return layout(h1("Still thinking it over?") + p("Hi %s, your pairs are still waiting for you:" % (name or "there")) + items(lines, imgs)
                  + totals([("Total", money(subtotal), True)]) + p("Sizes can sell out, so don't wait too long.", True) + button(site() + "/#cart", "Finish my order"), "You left something behind")


def campaign_email(subject, body_text, cta_label, cta_url, unsub_url):
    paras = "".join(p(chunk) for chunk in str(body_text).split("\n\n") if chunk.strip())
    return layout(h1(subject) + paras + (button(cta_url, cta_label) if cta_label and cta_url else button(site(), "Shop now")), subject, unsub_url)


def arrival_email(product, cid, url, unsub_url):
    return layout(h1("New arrival") + p("Fresh off the shelf at MANDILAS ONLINE:") + product_card(product["name"], product["price"], cid, url, "View shoe", product.get("colour", "")),
                  "New: " + product["name"], unsub_url)


def subscribed_email(unsub_url):
    return layout(h1("You're on the list") + p("Thanks for joining. You'll be first to hear about new arrivals, limited drops and offers.") + button(site() + "/#shop", "Browse the catalog"),
                  "Welcome to the House of Feet", unsub_url)


def lowstock_email(alerts, admin_url):
    rows = "".join("<tr><td style=\"padding:8px 0;border-bottom:1px solid #e6e1d4;font-family:%s;font-size:14px\">%s</td><td align=\"right\" style=\"padding:8px 0;border-bottom:1px solid #e6e1d4;font-family:%s;font-size:14px;color:%s\"><b>%s</b></td></tr>"
                   % (FONT, e(a["name"] + (" (size %s)" % a["size"] if a.get("size") else "")), FONT, BRAND, "SOLD OUT" if a["left"] <= 0 else "%d left" % a["left"]) for a in alerts)
    return layout(h1("Low stock alert") + p("These pairs are running low:") + '<table width="100%%" cellpadding="0" cellspacing="0">%s</table>' % rows + button(admin_url, "Restock now"), "Low stock alert")


def weekly_email(rep, admin_url):
    cards = "".join('<td align="center" style="padding:14px 6px;background:#f6f3ec;border:3px solid #fff"><div style="font-family:%s;font-size:11px;color:#6b6a64;text-transform:uppercase;letter-spacing:1px">%s</div><div style="font-family:%s;font-size:22px;color:%s;padding-top:4px"><b>%s</b></div></td>'
                    % (FONT, e(k), FONT, BRAND, v) for k, v in [("Revenue", money(rep["revenue"])), ("Orders", rep["orders"]), ("Avg order", money(rep["aov"]))])
    top = "".join("<tr><td style=\"padding:7px 0;border-bottom:1px solid #e6e1d4;font-family:%s;font-size:14px\">%s</td><td align=\"right\" style=\"font-family:%s;font-size:14px;border-bottom:1px solid #e6e1d4\"><b>&times;%s</b></td></tr>"
                  % (FONT, e(t["name"]), FONT, t["qty"]) for t in rep["top"]) or "<tr><td style=\"font-family:%s;color:#6b6a64\">No sales this week.</td></tr>" % FONT
    return layout(h1("Your week at a glance") + p("Last %d days" % rep["days"], True) + '<table width="100%%" cellpadding="0" cellspacing="0"><tr>%s</tr></table>' % cards
                  + '<h2 style="font-family:%s;font-size:16px;text-transform:uppercase;margin:22px 0 6px">Top sellers</h2><table width="100%%" cellpadding="0" cellspacing="0">%s</table>' % (FONT, top)
                  + button(admin_url, "Open full report"), "Weekly sales report")
