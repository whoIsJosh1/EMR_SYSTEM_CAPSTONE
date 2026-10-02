# ============================================================
# utils/email_utils.py - Email Sending Utilities
# Para sa pagpapadala ng OTP sa email ng Admin
# Gumagamit ng aiosmtplib para sa async email sending
# ============================================================

import os
import html
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType

load_dotenv()

# Kunin ang SMTP settings mula sa environment variables
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM    = os.getenv("EMAIL_FROM", "noreply@district1health.gov.ph")

# Pinalitan ang global settings gamit ang Mailtrap port 2525 para iwas network blocking
SMTP_HOST     = "sandbox.smtp.mailtrap.io"
SMTP_PORT     = 2525
SMTP_USER     = "661b70cd21e5f4"          # Iyong Mailtrap Username
SMTP_PASSWORD = "5a2d5a161a7310"          # Iyong Mailtrap Password
EMAIL_FROM    = "emr-system@vientereales.gov.ph"

# Optional: link ng login page. Ilagay sa .env, hal. APP_LOGIN_URL=http://localhost:8000/
# Kapag walang laman, hindi lalabas ang "Mag-login Ngayon" button sa email.
APP_LOGIN_URL = os.getenv("APP_LOGIN_URL", "")

async def send_temporary_password_email(email: str, name: str, role: str, temp_password: str):
    """
    Sends an email to the newly registered healthcare worker containing their 
    temporary credentials via Mailtrap Sandbox.
    """
    conf = ConnectionConfig(
        MAIL_USERNAME=SMTP_USER,
        MAIL_PASSWORD=SMTP_PASSWORD,
        MAIL_FROM=EMAIL_FROM,
        MAIL_PORT=SMTP_PORT,
        MAIL_SERVER=SMTP_HOST,
        MAIL_STARTTLS=True,
        MAIL_SSL_TLS=False,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True
    )

    # I-escape ang lahat ng galing sa labas para hindi masira ang HTML
    # (hal. kapag may "&" o "<" ang temporary password, ito ang laging lalabas nang tama).
    safe_name     = html.escape(name)
    safe_email    = html.escape(email)
    safe_role     = html.escape(role)
    safe_password = html.escape(temp_password)

    login_button = ""
    if APP_LOGIN_URL:
        login_button = (
            '<div style="text-align:center;margin:26px 0 4px">'
            f'<a href="{html.escape(APP_LOGIN_URL, quote=True)}" '
            'style="display:inline-block;background:#0d2272;color:#ffffff;text-decoration:none;'
            'font-weight:700;font-size:15px;padding:13px 32px;border-radius:8px">'
            'Mag-login Ngayon</a></div>'
        )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.3.1/css/all.min.css" rel="stylesheet">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f7f6; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }}
            .wrapper {{ width: 100%; table-layout: fixed; background-color: #ecf2f8; padding-bottom: 40px; padding-top: 40px; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border: none; border-radius: 12px; overflow: hidden; box-shadow: -8px -8px 12px rgba(255, 255, 255, 0.3), 8px  8px 12px rgba(0, 0, 0, 0.2); }}
            .header {{ background: linear-gradient(145deg, #c8d4f0 0%, #8fa8e0 18%, #4a6cbe 36%, #1e3d9e 52%, #0d2272 68%, #050e40 85%, #020930 100%); padding: 35px 20px; text-align: center; }}
            .header h1 {{ color: #ffffff; margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; }}
            .header p {{ color: #e2f0d9; margin: 8px 0 0; font-size: 14px; letter-spacing: 1px; }}
            .content {{ padding: 40px 35px; color: #333333; line-height: 1.6; }}
            .welcome-text {{ font-size: 18px; color: #0a4f76; margin-top: 0; margin-bottom: 15px; }}
            .intro-p {{ font-size: 15px; color: #555555; margin-bottom: 25px; }}
            .cred-box {{ background-color: #f8fafc; border: 1px solid #e2e8f0; border-left: 5px solid #1a8a5e; border-radius: 8px; padding: 25px; margin: 25px 0; }}
            .cred-title {{ font-size: 13px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin-top: 0; margin-bottom: 15px; }}
            .cred-row {{ margin-bottom: 12px; font-size: 15px; }}
            .cred-row:last-child {{ margin-bottom: 0; }}
            .cred-label {{ color: #475569; font-weight: 600; display: inline-block; width: 150px; }}
            .cred-value {{ color: #0f172a; font-family: monospace; font-size: 15px; }}
            .password-highlight {{ background-color: #f1f5f9; border: 1px dashed #cbd5e1; color: #dc2626; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 16px; letter-spacing: 0.5px; display: inline-block; transition: all 0.2s ease; -webkit-user-select:all;user-select:all;cursor:text; }}
            .role-badge {{ display: inline-block; background-color: #e0f2fe; color: #0369a1; font-size: 12px; font-weight: 700; padding: 4px 10px; border-radius: 50px; text-transform: uppercase; letter-spacing: 0.5px; }}
            .pw-card {{ margin: 0 0 8px; }}
            .pw-title {{ font-size: 13px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin: 0 0 10px; }}
            .pw-hint {{ font-size: 12.5px; color: #64748b; margin: 10px 0 0; line-height: 1.5; }}
            .steps {{ font-size: 14px; color: #475569; margin: 22px 0 0; padding-left: 20px; line-height: 1.6; }}
            .notice-box {{ background-color: #fffbec; border: 1px solid #ffe0b2; border-radius: 8px; padding: 15px 20px; margin-top: 30px; }}
            .notice-text {{ color: #b7791f; font-size: 13px; margin: 0; font-weight: 500; }}
            .strong-text {{ display: inline-block; margin-bottom: 4px; }}
            .footer {{ background-color: #f8fafc; padding: 25px; text-align: center; color: #64748b; font-size: 12px; border-top: 1px solid #f1f5f9; }}
            .footer p {{ margin: 4px 0; }}
        </style>
    </head>
    <body>
        <div class="wrapper">
            <div class="container">
                <!-- Header Component -->
                <div class="header">
                    <h1><i class="fa-solid fa-hospital-user"></i> Barangay Veinte Reales</h1>
                    <p>Electronic Medical Records (EMR) System</p>
                </div>
                
                <!-- Main Body Content -->
                <div class="content">
                    <p class="welcome-text">Mabuhay, <strong>{safe_name}</strong>!</p>
                    <p class="intro-p">Matagumpay na nairerehistro ng System Administrator ang iyong account bilang isang opisyal na kawani sa ating tanggapan. Maaari mo nang gamitin ang mga sumusunod na temporary credentials para sa iyong paunang login:</p>
                    
                    <!-- Secured Credentials Grid Box -->
                    <div class="cred-box">
                        <p class="cred-title">Account Access Information</p>
                        <div class="cred-row">
                            <span class="cred-label">Assigned Role:</span>
                            <span class="role-badge">{safe_role}</span>
                        </div>
                        <div class="cred-row">
                            <span class="cred-label">Email Address:</span>
                            <span class="cred-value"><strong>{safe_email}</strong></span>
                        </div>
                        <div class="cred-row">
                            <span class="cred-label">Temp Password:</span>
                            <span>
                                <code class="password-highlight" 
                                      id="tempPasswordCode"
                                      onclick="copyToClipboard('{safe_password}')" 
                                      style="cursor: pointer;" 
                                      title="Click to copy">
                                    {safe_password}
                                </code>
                            </span>
                        </div>
                    </div>
                    <p class="pw-hint"><strong>Paano kopyahin:</strong> i-<strong>triple-click</strong> ang password (o <strong>i-long-press</strong> sa phone) para mapili ang buo, tapos piliin ang <strong>Copy</strong>. I-paste ito sa "Temporary Password" field sa unang login.</p>

                    {login_button}

                    <ol class="steps">
                        <li>Buksan ang EMR System at ilagay ang iyong email at ang temporary password sa itaas.</li>
                        <li>Magtakda ng sarili at bagong password kapag hiningi ng system.</li>
                    </ol>

                    <!-- Compliance and Security Warnings -->
                    <div class="notice-box">
                        <div class="notice-text">
                            <div class="strong-text">
                                <i class="fa-solid fa-triangle-exclamation"></i> <strong>PAALALANG SEGURIDAD:</strong> 
                            </div>
                            <p style="margin: 4px 0 0 0;">Para sa proteksyon ng data ng ating mga pasyente, hihingan ka ng system na palitan kaagad ang temporary password na ito ng iyong sarili at permanenteng password sa sandaling makapag-login ka sa unang pagkakataon.</p>
                        </div>
                    </div>
                </div>
                
                <!-- System Footer Footer -->
                <div class="footer">
                    <p>This is an automated system-generated notification from District 1 Health Office.</p>
                    <p>Please do not reply directly to this email address.</p>
                    <p>&copy; {2026} Barangay Veinte Reales Health Center. All rights reserved.</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    message = MessageSchema(
        subject="EMR System - Account Activated and Temporary Password",
        recipients=[email],
        body=html_content,
        subtype=MessageType.html
    )

    try:
        fm = FastMail(conf)
        await fm.send_message(message)
        print("======== [SUCCESS] EMAIL SENT SUCCESSFULLY FROM EMAIL_UTILS.PY ========")
    except Exception as e:
        print(f"======== [ERROR] EMAIL_UTILS FAILED: {str(e)} ========")

def send_otp_email(recipient_email: str, recipient_name: str, otp_code: str, purpose: str = "registration") -> bool:
    """
    Magpadala ng OTP sa email ng Admin.
    Ginagamit ito bago mag-finalize ng bagong BHW registration.
    
    Parameters:
        recipient_email: Email address ng tatanggap
        recipient_name: Pangalan ng tatanggap
        otp_code: Ang OTP na dapat ipadala
        purpose: Kung para saan ang OTP ('registration' o 'unlock')
    
    Returns True kung matagumpay, False kung may error.
    """
    try:
        # Gumawa ng email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"EMR System - Your OTP for {purpose.title()}"
        msg["From"]    = EMAIL_FROM
        msg["To"]      = recipient_email

        # Depende sa purpose, iba ang nilalaman ng email
        if purpose == "registration":
            action_text = "register a new Barangay Health Worker (BHW) account"
        else:
            action_text = "perform this action"

        # HTML version ng email - mas maganda ang hitsura
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                .header {{ background: linear-gradient(135deg, #0a4f76, #1a8a5e); padding: 30px; text-align: center; }}
                .header h1 {{ color: white; margin: 0; font-size: 24px; }}
                .header p {{ color: #cce5ff; margin: 5px 0 0; }}
                .body {{ padding: 30px; }}
                .otp-box {{ background: #f0f7ff; border: 2px solid #0a4f76; border-radius: 8px; text-align: center; padding: 20px; margin: 20px 0; }}
                .otp-code {{ font-size: 36px; font-weight: bold; color: #0a4f76; letter-spacing: 8px; }}
                .warning {{ color: #dc3545; font-size: 13px; margin-top: 15px; }}
                .footer {{ background: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🏥 District 1 Health EMR System</h1>
                    <p>One-Time Password (OTP) Verification</p>
                </div>
                <div class="body">
                    <p>Dear <strong>{recipient_name}</strong>,</p>
                    <p>You have requested to <strong>{action_text}</strong>. 
                    Please use the OTP below to proceed:</p>
                    
                    <div class="otp-box">
                        <div class="otp-code">{otp_code}</div>
                        <p style="margin: 10px 0 0; color: #555;">This OTP is valid for <strong>10 minutes</strong> only.</p>
                    </div>
                    
                    <p class="warning">
                        ⚠️ <strong>Security Notice:</strong> Do not share this OTP with anyone. 
                        The system will never ask for this code via phone or chat. 
                        If you did not request this OTP, please ignore this email and 
                        consider changing your password immediately.
                    </p>
                </div>
                <div class="footer">
                    <p>This is an automated message from the District 1 Health EMR System.</p>
                    <p>© {2024} District 1 Health Office. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Plain text fallback para sa mga email client na hindi sumusuporta ng HTML
        text_content = f"""
        District 1 Health EMR System - OTP Verification
        
        Dear {recipient_name},
        
        Your OTP for {action_text} is: {otp_code}
        
        This OTP is valid for 10 minutes only.
        
        DO NOT share this OTP with anyone.
        """

        # I-attach ang dalawang versions ng email
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        # Ipadala ang email gamit ang SMTP
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()  # I-enable ang TLS encryption
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipient_email, msg.as_string())

        print(f"✅ OTP email sent successfully to {recipient_email}")
        return True

    except Exception as e:
        print(f"❌ Failed to send OTP email: {e}")
        return False


def send_account_created_email(recipient_email: str, recipient_name: str, temp_password: str) -> bool:
    """
    Magpadala ng notification email sa bagong BHW account.
    Kasama ang temporary password na dapat palitan agad.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "EMR System - Your Account Has Been Created"
        msg["From"]    = EMAIL_FROM
        msg["To"]      = recipient_email

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background: #f5f5f5; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; overflow: hidden; }}
                .header {{ background: linear-gradient(135deg, #0a4f76, #1a8a5e); padding: 30px; text-align: center; }}
                .header h1 {{ color: white; margin: 0; }}
                .body {{ padding: 30px; }}
                .cred-box {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 15px; margin: 15px 0; }}
                .footer {{ background: #f8f9fa; padding: 20px; text-align: center; color: #6c757d; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🏥 District 1 Health EMR System</h1>
                </div>
                <div class="body">
                    <p>Dear <strong>{recipient_name}</strong>,</p>
                    <p>Your BHW account has been created by the System Administrator.</p>
                    
                    <div class="cred-box">
                        <p><strong>Email:</strong> {recipient_email}</p>
                        <p><strong>Temporary Password:</strong> {temp_password}</p>
                    </div>
                    
                    <p>⚠️ Please log in immediately and change your password.</p>
                </div>
                <div class="footer">
                    <p>District 1 Health EMR System</p>
                </div>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, recipient_email, msg.as_string())

        return True
    except Exception as e:
        print(f"❌ Failed to send account created email: {e}")
        return False