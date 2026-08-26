package com.sensocrypt.auth

import android.app.Activity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.AuthPhoneApi
import com.sensocrypt.net.AuthPhoneApiException
import kotlinx.coroutines.launch

private enum class AuthPhase { DETAILS, OTP, LOADING }

/** Phone-number signup/login (v2). The OTP itself is sent and verified entirely by Firebase
 * (FirebasePhoneAuth) -- this screen never sees or handles the actual SMS code beyond
 * collecting what the user typed and handing it to Firebase; only the resulting ID token
 * goes to our own backend. */
@Composable
fun AuthScreen(onAuthenticated: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val firebaseAuth = remember { FirebasePhoneAuth() }
    val authApi = remember { AuthPhoneApi() }
    val userSession = remember { UserSession(context) }

    var isSignup by remember { mutableStateOf(true) }
    var phone by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var otp by remember { mutableStateOf("") }
    var phase by remember { mutableStateOf(AuthPhase.DETAILS) }
    var errorText by remember { mutableStateOf<String?>(null) }

    fun completeWithFirebaseToken(idToken: String) {
        phase = AuthPhase.LOADING
        scope.launch {
            try {
                val response = if (isSignup) {
                    authApi.signup(idToken, name.trim(), email.trim())
                } else {
                    authApi.login(idToken)
                }
                userSession.userId = response.user_id
                userSession.authToken = response.token
                userSession.name = name.trim().ifBlank { null }
                onAuthenticated()
            } catch (e: AuthPhoneApiException) {
                errorText = when (e.httpCode) {
                    409 -> "An account already exists for this number -- try Log In instead"
                    404 -> "No account for this number yet -- try Sign Up instead"
                    else -> "Something went wrong: ${e.body}"
                }
                phase = AuthPhase.OTP
            } catch (e: Exception) {
                errorText = e.message ?: "Something went wrong"
                phase = AuthPhase.OTP
            }
        }
    }

    fun sendCode() {
        errorText = null
        val e164 = phone.trim()
        if (!e164.startsWith("+") || e164.length < 8) {
            errorText = "Enter your number with country code, e.g. +919876543210"
            return
        }
        if (isSignup && (name.isBlank() || email.isBlank())) {
            errorText = "Name and email are required to sign up"
            return
        }
        phase = AuthPhase.LOADING
        firebaseAuth.sendOtp(
            phoneNumber = e164,
            activity = context as Activity,
            onCodeSent = { phase = AuthPhase.OTP },
            onAutoVerified = { credential ->
                scope.launch {
                    try {
                        val idToken = firebaseAuth.signInWithCredential(credential)
                        completeWithFirebaseToken(idToken)
                    } catch (e: Exception) {
                        errorText = e.message ?: "Verification failed"
                        phase = AuthPhase.DETAILS
                    }
                }
            },
            onError = { message ->
                errorText = message
                phase = AuthPhase.DETAILS
            },
        )
    }

    fun verifyCode() {
        errorText = null
        phase = AuthPhase.LOADING
        scope.launch {
            try {
                val idToken = firebaseAuth.verifyOtp(otp.trim())
                completeWithFirebaseToken(idToken)
            } catch (e: Exception) {
                errorText = "Incorrect code -- try again"
                phase = AuthPhase.OTP
            }
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(
            modifier = Modifier.fillMaxSize().padding(28.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(Icons.Filled.Shield, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.height(40.dp))
            Spacer(Modifier.height(12.dp))
            Text("SensoCrypt", style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(4.dp))
            Text(
                if (isSignup) "Create your account" else "Log in",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(24.dp))

            when (phase) {
                AuthPhase.DETAILS -> {
                    if (isSignup) {
                        OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(12.dp))
                        OutlinedTextField(value = email, onValueChange = { email = it }, label = { Text("Email") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                        Spacer(Modifier.height(12.dp))
                    }
                    OutlinedTextField(
                        value = phone,
                        onValueChange = { phone = it },
                        label = { Text("Phone number") },
                        placeholder = { Text("+919876543210") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(20.dp))
                    Button(
                        onClick = { sendCode() },
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Text("Send Code", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
                AuthPhase.OTP -> {
                    Text(
                        "Enter the code sent to $phone",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(12.dp))
                    OutlinedTextField(
                        value = otp,
                        onValueChange = { otp = it },
                        label = { Text("Verification code") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(Modifier.height(20.dp))
                    Button(
                        onClick = { verifyCode() },
                        modifier = Modifier.fillMaxWidth().height(52.dp),
                        shape = RoundedCornerShape(14.dp),
                    ) {
                        Text("Verify", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
                AuthPhase.LOADING -> {
                    CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                }
            }

            errorText?.let {
                Spacer(Modifier.height(12.dp))
                Text(it, color = MaterialTheme.colorScheme.error, fontSize = 13.sp, textAlign = TextAlign.Center)
            }

            Spacer(Modifier.height(20.dp))
            TextButton(onClick = {
                isSignup = !isSignup
                phase = AuthPhase.DETAILS
                errorText = null
            }) {
                Text(if (isSignup) "Already have an account? Log in" else "New here? Sign up")
            }
        }
    }
}
