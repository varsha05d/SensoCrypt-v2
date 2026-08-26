package com.sensocrypt.call

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.unit.dp
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.CallsApi
import kotlinx.coroutines.launch

@Composable
fun IncomingCallScreen(
    callId: String,
    callerName: String,
    onAccepted: () -> Unit,
    onDeclinedOrError: (message: String?) -> Unit,
) {
    val context = LocalContext.current
    val userSession = remember { UserSession(context) }
    val callsApi = remember { CallsApi() }
    val scope = rememberCoroutineScope()

    var busy by remember { mutableStateOf(false) }

    fun accept() {
        val authToken = userSession.authToken ?: return
        busy = true
        scope.launch {
            try {
                callsApi.acceptCall(callId, authToken)
                onAccepted()
            } catch (e: Exception) {
                onDeclinedOrError("Couldn't accept the call: ${e.message}")
            }
        }
    }

    fun decline() {
        val authToken = userSession.authToken ?: return
        busy = true
        scope.launch {
            try {
                callsApi.declineCall(callId, authToken)
            } catch (e: Exception) {
                // Best-effort -- either way, leave the incoming-call screen.
            }
            onDeclinedOrError(null)
        }
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
                modifier = Modifier.height(72.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.12f)).padding(14.dp),
            )
            Spacer(Modifier.height(20.dp))
            Text(callerName, color = Color.White, style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.height(6.dp))
            Text("Incoming SensoCrypt call", color = Color.White.copy(alpha = 0.7f))
            Spacer(Modifier.height(48.dp))

            if (busy) {
                CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        IconButton(
                            onClick = { decline() },
                            modifier = Modifier.height(64.dp).clip(CircleShape).background(MaterialTheme.colorScheme.error),
                        ) {
                            Icon(Icons.Filled.CallEnd, contentDescription = "Decline", tint = Color.White, modifier = Modifier.height(28.dp))
                        }
                        Spacer(Modifier.height(8.dp))
                        Text("Decline", color = Color.White.copy(alpha = 0.7f))
                    }
                    Spacer(Modifier.width(56.dp))
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        IconButton(
                            onClick = { accept() },
                            modifier = Modifier.height(64.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary),
                        ) {
                            Icon(Icons.Filled.Call, contentDescription = "Accept", tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.height(28.dp))
                        }
                        Spacer(Modifier.height(8.dp))
                        Text("Accept", color = Color.White.copy(alpha = 0.7f))
                    }
                }
            }
        }
    }
}
