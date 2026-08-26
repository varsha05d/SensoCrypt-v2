package com.sensocrypt.call

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.CallsApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val STATUS_POLL_INTERVAL_MS = 1_500L
private const val RING_TIMEOUT_MS = 45_000L

@Composable
fun DialerScreen(onCallAccepted: (callId: String) -> Unit, onExit: () -> Unit) {
    val context = LocalContext.current
    val userSession = remember { UserSession(context) }
    val callsApi = remember { CallsApi() }
    val scope = rememberCoroutineScope()

    var phoneNumber by remember { mutableStateOf("") }
    var placing by remember { mutableStateOf(false) }
    var ringingCallId by remember { mutableStateOf<String?>(null) }
    var errorText by remember { mutableStateOf<String?>(null) }

    fun placeCall() {
        val authToken = userSession.authToken ?: return
        errorText = null
        placing = true
        scope.launch {
            try {
                val response = callsApi.placeCall(phoneNumber.trim(), authToken)
                placing = false
                ringingCallId = response.call_id
            } catch (e: Exception) {
                placing = false
                errorText = when {
                    e.message?.contains("404") == true -> "No SensoCrypt account for that number"
                    e.message?.contains("400") == true -> "You can't call yourself"
                    else -> "Couldn't place the call: ${e.message}"
                }
            }
        }
    }

    val activeCallId = ringingCallId
    if (activeCallId != null) {
        RingingScreen(
            callId = activeCallId,
            phoneNumber = phoneNumber.trim(),
            onAccepted = { onCallAccepted(activeCallId) },
            onEnded = { message -> errorText = message; ringingCallId = null },
        )
        return
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        IconButton(
            onClick = onExit,
            modifier = Modifier.align(Alignment.TopStart).systemBarsPadding().padding(12.dp)
                .clip(CircleShape).background(Color.Black.copy(alpha = 0.45f)),
        ) {
            Icon(Icons.Filled.ArrowBack, contentDescription = "Back", tint = Color.White)
        }

        Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 28.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(Icons.Filled.Videocam, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.height(40.dp))
            Spacer(Modifier.height(12.dp))
            Text("Call by phone number", style = MaterialTheme.typography.titleLarge, color = Color.White)
            Spacer(Modifier.height(4.dp))
            Text(
                "Enter the number of another SensoCrypt user to call them.",
                style = MaterialTheme.typography.bodyLarge,
                color = Color.White.copy(alpha = 0.7f),
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(24.dp))

            OutlinedTextField(
                value = phoneNumber,
                onValueChange = { phoneNumber = it },
                label = { Text("Phone number") },
                placeholder = { Text("+919876543210") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )

            errorText?.let {
                Spacer(Modifier.height(12.dp))
                Text(it, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
            }

            Spacer(Modifier.height(20.dp))

            Button(
                onClick = { placeCall() },
                enabled = !placing && phoneNumber.isNotBlank(),
                modifier = Modifier.fillMaxWidth().height(54.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.primary),
            ) {
                if (placing) {
                    CircularProgressIndicator(modifier = Modifier.height(20.dp), color = MaterialTheme.colorScheme.onPrimary)
                } else {
                    Text("Call", fontSize = 16.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onPrimary)
                }
            }
        }
    }
}

@Composable
private fun RingingScreen(
    callId: String,
    phoneNumber: String,
    onAccepted: () -> Unit,
    onEnded: (message: String?) -> Unit,
) {
    val context = LocalContext.current
    val userSession = remember { UserSession(context) }
    val callsApi = remember { CallsApi() }

    LaunchedEffect(callId) {
        val authToken = userSession.authToken ?: run { onEnded("Not logged in"); return@LaunchedEffect }
        val deadline = System.currentTimeMillis() + RING_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            delay(STATUS_POLL_INTERVAL_MS)
            val status = try { callsApi.getCallStatus(callId, authToken) } catch (e: Exception) { null }
            when (status?.state) {
                "VERIFYING", "CONNECTED" -> { onAccepted(); return@LaunchedEffect }
                "DECLINED" -> { onEnded("$phoneNumber declined the call"); return@LaunchedEffect }
                "FAILED_VERIFICATION" -> { onEnded("Call ended -- verification failed"); return@LaunchedEffect }
                "ENDED" -> { onEnded("Call ended"); return@LaunchedEffect }
            }
        }
        onEnded("No answer")
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        Column(
            modifier = Modifier.fillMaxSize().padding(28.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(
                Icons.Filled.Person,
                contentDescription = null,
                tint = Color.White,
                modifier = Modifier.height(64.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.12f)).padding(12.dp),
            )
            Spacer(Modifier.height(20.dp))
            Text(phoneNumber, color = Color.White, style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(6.dp))
            Text("Calling…", color = Color.White.copy(alpha = 0.7f))
            Spacer(Modifier.height(40.dp))

            IconButton(
                onClick = { onEnded(null) },
                modifier = Modifier.height(64.dp).clip(CircleShape).background(MaterialTheme.colorScheme.error),
            ) {
                Icon(Icons.Filled.CallEnd, contentDescription = "Cancel", tint = Color.White, modifier = Modifier.height(28.dp))
            }
        }
    }
}
