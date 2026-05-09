import os
from django.utils.html import escape

def get_app_name():
    return os.environ.get("VITE_APP_NAME")

def get_base_url():
    return os.environ.get("VITE_API_BASE_URL_CERT")

def _get_base_html_template(safe_app_name, banner_html, body_html, button_html=None):
    """
    Estructura base compartida para todos los correos.
    Mantiene el estilo, la animación y el banner sólido (sin puntos).
    """
    return f"""<!DOCTYPE html>
    <html lang="es">
        <head>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1" />
            <title>{safe_app_name}</title>
            <style>
                @keyframes welcomePulse {{
                    0% {{ transform: translateY(6px) scale(0.98); opacity: 0.75; }}
                    60% {{ transform: translateY(0) scale(1.02); opacity: 1; }}
                    100% {{ transform: translateY(0) scale(1); opacity: 1; }}
                }}
                .welcome-text {{
                    display: inline-block;
                    animation: welcomePulse 2.6s ease-in-out infinite;
                }}
                @media (prefers-reduced-motion: reduce) {{
                    .welcome-text {{ animation: none; }}
                }}
            </style>
        </head>
        <body style="margin:0;padding:0;background:#f8f9fd;color:#1a1d23;">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8f9fd;">
                <tr>
                    <td align="center" style="padding:32px 16px;">
                        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e5e4e7;border-radius:18px;overflow:hidden;">
                            
                            <tr>
                                <td style="padding:40px 28px; background-color: #564AA1;">
                                    <div style="font-family:'Trebuchet MS',sans-serif;font-size:13px;letter-spacing:1.5px;text-transform:uppercase;color:#ffffff;opacity:0.9;margin-bottom:8px;">{safe_app_name}</div>
                                    {banner_html}
                                </td>
                            </tr>
                            
                            <tr>
                                <td style="padding:32px 28px 10px;font-family:'Trebuchet MS',sans-serif;font-size:16px;line-height:1.6;color:#1a1d23;">
                                    {body_html}
                                </td>
                            </tr>

                            {f'''<tr>
                                <td align="center" style="padding:10px 28px 40px;">
                                    <table role="presentation" cellspacing="0" cellpadding="0">
                                        <tr>
                                            <td align="center" bgcolor="#564AA1" style="border-radius:10px;">
                                                {button_html}
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>''' if button_html else ''}

                            <tr>
                                <td style="padding:0 28px 28px;font-family:'Trebuchet MS',sans-serif;font-size:13px;color:#8e94a9;border-top:1px solid #f1f1f4;padding-top:20px;">
                                    Si no solicitaste este correo, puedes ignorar este mensaje.
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
    </html>"""


def build_welcome_email(first_name=None, last_name=None, app_name=None):
    app_name = app_name or get_app_name()
    base_url = get_base_url()
    safe_app_name = escape(app_name)
    
    full_name = " ".join(part for part in [first_name, last_name] if part)
    raw_name = full_name.strip()
    safe_name = escape(raw_name)

    # Textos originales
    greeting = f"Hola {raw_name}," if raw_name else "Hola,"
    html_greeting = f"Hola, {safe_name}," if safe_name else "Hola,"
    subject = f"¡Bienvenido a {app_name}!"
    
    text_body = (
        f"{greeting}\n\n"
        f"Gracias por registrarte en {app_name}."
        f"Ya puedes iniciar sesión para comenzar a gestionar tus proyectos y estimaciones.\n\n"
        "Si no reconoces este registro, puedes ignorar este mensaje."
    )

    # Componentes HTML
    banner = f"""<h1 style="margin:0;font-family:Georgia,serif;font-size:32px;line-height:1.2;color:#ffffff;">
                    <span class="welcome-text">¡Bienvenido a {safe_app_name}!</span>
                 </h1>
                 <p style="margin:12px 0 0;font-family:'Trebuchet MS',sans-serif;font-size:16px;color:#f0eeff;opacity:0.95;">Tu cuenta ya está activa.</p>"""
    
    body = f"""<p style="margin:0 0 16px; font-size:18px; font-weight:bold;">{html_greeting}</p>
               <p style="margin:0 0 20px;">Gracias por registrarte en <strong>{safe_app_name}</strong>. Ya puedes iniciar sesión para comenzar a gestionar tus proyectos y estimaciones.</p>"""
    
    button = f"""<a href="{base_url}" target="_blank" style="display:inline-block;padding:16px 32px;font-family:'Trebuchet MS',sans-serif;font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;">
                    Acceder a la Aplicación
                </a>"""

    html_body = _get_base_html_template(safe_app_name, banner, body, button)
    return subject, text_body, html_body


def build_reset_email(code, first_name=None, app_name=None):
    app_name = app_name or get_app_name()
    safe_app_name = escape(app_name)
    safe_name = escape(first_name) if first_name else ""
    
    subject = f"Código de recuperación - {app_name}"
    html_greeting = f"Hola, {safe_name}," if safe_name else "Hola,"
    
    banner = f"""<h1 style="margin:0;font-family:Georgia,serif;font-size:32px;line-height:1.2;color:#ffffff;">
                    <span class="welcome-text">Recuperar contraseña</span>
                 </h1>
                 <p style="margin:12px 0 0;font-family:'Trebuchet MS',sans-serif;font-size:16px;color:#f0eeff;opacity:0.95;">Has solicitado un restablecimiento de contraseña.</p>"""
    
    body = f"""<p style="margin:0 0 16px; font-size:18px; font-weight:bold;">{html_greeting}</p>
               <p style="margin:0 0 20px;">Utiliza el siguiente código para validar tu identidad. Este código es temporal y expirará pronto.</p>
               <div style="text-align:center; padding:20px; background:#f0eeff; border-radius:12px; margin:20px 0;">
                    <span style="font-family:monospace; font-size:36px; font-weight:bold; color:#564AA1; letter-spacing:10px;">{code}</span>
               </div>"""

    text_body = f"{html_greeting}\n\nTu código de recuperación es: {code}\n\nSi no solicitaste esto, ignora el mensaje."

    html_body = _get_base_html_template(safe_app_name, banner, body, button_html=None)
    return subject, text_body, html_body