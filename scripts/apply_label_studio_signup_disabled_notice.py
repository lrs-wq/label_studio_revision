from __future__ import annotations

from pathlib import Path


NOTICE = "注册功能已关闭，请使用现有账号登录。"


def signup_disabled_script(button_id: str, message_id: str) -> str:
    return f"""<script nonce="{{{{request.csp_nonce}}}}">
    (() => {{
      const button = document.getElementById("{button_id}");
      const message = document.getElementById("{message_id}");
      const text = "{NOTICE}";
      if (!button) return;
      button.addEventListener("click", () => {{
        if (message) {{
          message.textContent = text;
        }}
        window.alert(text);
      }});
    }})();
  </script>"""


def label_studio_package_dir() -> Path:
    import label_studio

    return Path(label_studio.__file__).resolve().parent


def replace_once(path: Path, old: str, new: str) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace('role="status" aria-live="polite"', 'role="alert" aria-live="assertive"')
    if normalized != text:
        path.write_text(normalized, encoding="utf-8")
        text = normalized
    if new in text:
        return f"already patched: {path}"
    if old not in text:
        raise RuntimeError(f"Expected template block was not found in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    return f"patched: {path}"


def patch_new_ui_login(template_dir: Path) -> str:
    path = template_dir / "users" / "new-ui" / "user_login.html"
    old = """  {% if not settings.DISABLE_SIGNUP_WITHOUT_LINK %}
  <div class="text-wrapper">
    <p class="">Don't have an account?</p>
    <a href="{% url 'user-signup' %}{% querystring %}">Sign up</a>
  </div>
  {% endif %}"""
    new = f"""  <div class="text-wrapper">
    <p class="">Don't have an account?</p>
    {{% if not settings.DISABLE_SIGNUP_WITHOUT_LINK %}}
      <a href="{{% url 'user-signup' %}}{{% querystring %}}">Sign up</a>
    {{% else %}}
      <button type="button" id="signup-disabled-button" style="padding: 0; border: 0; background: transparent; color: #ff7557; text-decoration: underline; cursor: pointer; font: inherit;">Sign up</button>
      <p id="signup-disabled-message" role="alert" aria-live="assertive" style="margin: 8px 0 0; color: #d62728;"></p>
      {signup_disabled_script("signup-disabled-button", "signup-disabled-message")}
    {{% endif %}}
  </div>"""
    return replace_once(path, old, new)


def patch_old_ui_base(template_dir: Path) -> str:
    path = template_dir / "users" / "user_base.html"
    old = """  <div class="toggle">
    {% if not settings.DISABLE_SIGNUP_WITHOUT_LINK %}
        <a href="{{ settings.HOSTNAME }}/user/signup" class="{% if 'signup' in request.path %}active{% endif %}">Sign up</a>
        <a href="{{ settings.HOSTNAME }}/user/login" class="{% if 'login' in request.path %}active{% endif %}">Log in</a>
    {% endif %}
  </div>"""
    new = f"""  <div class="toggle">
    {{% if not settings.DISABLE_SIGNUP_WITHOUT_LINK %}}
        <a href="{{{{ settings.HOSTNAME }}}}/user/signup" class="{{% if 'signup' in request.path %}}active{{% endif %}}">Sign up</a>
        <a href="{{{{ settings.HOSTNAME }}}}/user/login" class="{{% if 'login' in request.path %}}active{{% endif %}}">Log in</a>
    {{% else %}}
        <button type="button" id="signup-disabled-toggle" class="{{% if 'signup' in request.path %}}active{{% endif %}}" style="border: 0; cursor: pointer;">Sign up</button>
        <a href="{{{{ settings.HOSTNAME }}}}/user/login" class="{{% if 'login' in request.path %}}active{{% endif %}}">Log in</a>
    {{% endif %}}
  </div>
  {{% if settings.DISABLE_SIGNUP_WITHOUT_LINK %}}
  <p id="signup-disabled-toggle-message" role="alert" aria-live="assertive" style="margin: 8px 0 0; color: #d62728;"></p>
  {signup_disabled_script("signup-disabled-toggle", "signup-disabled-toggle-message")}
  {{% endif %}}"""
    return replace_once(path, old, new)


def patch_new_ui_signup(template_dir: Path) -> list[str]:
    path = template_dir / "users" / "new-ui" / "user_signup.html"
    old_start = """{% block user_content %}
  <div class="form-wrapper">"""
    new_start = f"""{{% block user_content %}}
  {{% if settings.DISABLE_SIGNUP_WITHOUT_LINK and not token %}}
  <div class="form-wrapper">
    <h2>Sign Up</h2>
    <p id="signup-disabled-page-message" role="alert" aria-live="assertive" style="color: #d62728;">{NOTICE}</p>
    <button type="button" id="signup-disabled-page-button" class="lsf-button-ls lsf-button-ls_look_primary">Create Account</button>
  </div>
  <div class="text-wrapper">
    <p class="">Already have an account?</p>
    <a href="{{% url 'user-login' %}}{{% querystring %}}">Log in</a>
  </div>
  {signup_disabled_script("signup-disabled-page-button", "signup-disabled-page-message")}
  {{% else %}}
  <div class="form-wrapper">"""
    old_end = """  <script>
    document.querySelector("#how_find_us").addEventListener('change', function(e) {
      const isOther = e.target.value == '{{ elaborate }}';
      document.querySelector("#elaborateContainer").style.display = isOther ? 'block' : 'none';
    });
  </script>
{% endblock %}"""
    new_end = """  <script>
    document.querySelector("#how_find_us").addEventListener('change', function(e) {
      const isOther = e.target.value == '{{ elaborate }}';
      document.querySelector("#elaborateContainer").style.display = isOther ? 'block' : 'none';
    });
  </script>
  {% endif %}
{% endblock %}"""
    return [replace_once(path, old_start, new_start), replace_once(path, old_end, new_end)]


def patch_old_ui_signup(template_dir: Path) -> list[str]:
    path = template_dir / "users" / "user_signup.html"
    old_start = """{% block user_content %}

  <form id="signup-form\""""
    new_start = f"""{{% block user_content %}}

  {{% if settings.DISABLE_SIGNUP_WITHOUT_LINK and not token %}}
  <p id="signup-disabled-page-message" role="alert" aria-live="assertive" style="color: #d62728;">{NOTICE}</p>
  <p><button type="button" id="signup-disabled-page-button" class="lsf-button-ls lsf-button-ls_look_primary">Create Account</button></p>
  {signup_disabled_script("signup-disabled-page-button", "signup-disabled-page-message")}
  {{% else %}}
  <form id="signup-form\""""
    old_end = """  </form>

{% endblock %}"""
    new_end = """  </form>
  {% endif %}

{% endblock %}"""
    return [replace_once(path, old_start, new_start), replace_once(path, old_end, new_end)]


def main() -> None:
    package_dir = label_studio_package_dir()
    template_dir = package_dir / "users" / "templates"
    results = [
        patch_new_ui_login(template_dir),
        patch_old_ui_base(template_dir),
        *patch_new_ui_signup(template_dir),
        *patch_old_ui_signup(template_dir),
    ]
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
