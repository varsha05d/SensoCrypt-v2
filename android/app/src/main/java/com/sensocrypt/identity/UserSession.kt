package com.sensocrypt.identity

import android.content.Context

/** Persists the logged-in user's identity and auth token (issued by
 * /api/v1/auth/phone/{signup,login}) -- separate from IdentityStore, which is the
 * hardware-attested DEVICE identity, not the human account. A device can be enrolled
 * without anyone being logged in yet (enrollment happens automatically on first launch,
 * per v1); a user must explicitly sign up or log in with their phone number. */
class UserSession(context: Context) {
    private val prefs = context.getSharedPreferences("sensocrypt_user_session", Context.MODE_PRIVATE)

    var userId: String?
        get() = prefs.getString("user_id", null)
        set(value) = prefs.edit().putString("user_id", value).apply()

    var authToken: String?
        get() = prefs.getString("auth_token", null)
        set(value) = prefs.edit().putString("auth_token", value).apply()

    var name: String?
        get() = prefs.getString("name", null)
        set(value) = prefs.edit().putString("name", value).apply()

    val isLoggedIn: Boolean get() = userId != null && authToken != null

    fun clear() {
        prefs.edit().clear().apply()
    }
}
