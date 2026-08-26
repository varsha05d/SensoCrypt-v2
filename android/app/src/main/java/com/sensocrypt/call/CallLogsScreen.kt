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
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CallMade
import androidx.compose.material.icons.filled.CallReceived
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sensocrypt.identity.UserSession
import com.sensocrypt.net.CallLogEntry
import com.sensocrypt.net.CallsApi

@Composable
fun CallLogsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val userSession = remember { UserSession(context) }
    val callsApi = remember { CallsApi() }

    var logs by remember { mutableStateOf<List<CallLogEntry>?>(null) }
    var errorText by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        val authToken = userSession.authToken
        if (authToken == null) {
            errorText = "Not logged in"
            return@LaunchedEffect
        }
        try {
            logs = callsApi.callLogs(authToken)
        } catch (e: Exception) {
            errorText = "Couldn't load call logs: ${e.message}"
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(modifier = Modifier.fillMaxSize()) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.fillMaxWidth().systemBarsPadding().padding(12.dp),
            ) {
                IconButton(onClick = onBack) {
                    Icon(Icons.Filled.ArrowBack, contentDescription = "Back", tint = MaterialTheme.colorScheme.onBackground)
                }
                Spacer(Modifier.width(4.dp))
                Text("Call Logs", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onBackground)
            }

            when {
                errorText != null -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(errorText!!, color = MaterialTheme.colorScheme.error)
                }
                logs == null -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
                }
                logs!!.isEmpty() -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("No calls yet", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                else -> LazyColumn(modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
                    items(logs!!) { entry -> CallLogRow(entry) }
                }
            }
        }
    }
}

@Composable
private fun CallLogRow(entry: CallLogEntry) {
    val isFraud = entry.state == "FAILED_VERIFICATION"
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
    ) {
        Icon(
            if (entry.direction == "outgoing") Icons.Filled.CallMade else Icons.Filled.CallReceived,
            contentDescription = null,
            tint = if (isFraud) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.height(20.dp),
        )
        Spacer(Modifier.width(14.dp))
        Column(modifier = Modifier.fillMaxWidth()) {
            Text(
                entry.other_party_name.ifBlank { entry.other_party_phone },
                color = MaterialTheme.colorScheme.onBackground,
                fontWeight = FontWeight.Medium,
            )
            Spacer(Modifier.height(2.dp))
            if (isFraud) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(MaterialTheme.colorScheme.error.copy(alpha = 0.15f))
                        .padding(horizontal = 8.dp, vertical = 3.dp),
                ) {
                    Icon(Icons.Filled.Warning, contentDescription = null, tint = MaterialTheme.colorScheme.error, modifier = Modifier.height(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Likely Fraud", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.SemiBold)
                }
            } else {
                Text(callStateLabel(entry.state), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

private fun callStateLabel(state: String): String = when (state) {
    "CONNECTED" -> "Connected"
    "ENDED" -> "Ended"
    "DECLINED" -> "Declined"
    "RINGING" -> "No answer"
    "VERIFYING" -> "Verifying"
    else -> state
}
