package com.sensocrypt.capture

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager

data class TelemetryChunkData(
    val seq: Long,
    val frames: List<GrayFrame>,
    val gyro: List<SensorReading>,
    // Accelerometer (m/s^2, gravity included). Sent so a device WITHOUT a gyroscope (many
    // tablets and budget phones) can still prove the device is physically being moved.
    val accel: List<SensorReading> = emptyList(),
)

/**
 * Buffers frames + gyro for the live telemetry pipeline (Phase 4), drained into ~500ms
 * chunks (plan.md §4.5 CHUNK_MS). Deliberately separate from SessionRecorder even though
 * the buffering logic overlaps: that one accumulates a whole session for offline analysis,
 * this one drains continuously for streaming -- different enough lifecycles that sharing
 * one class would mean threading two modes through it. Worth revisiting if a third
 * consumer shows up.
 */
class LiveStreamer(context: Context) {
    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val frameBuffer = mutableListOf<GrayFrame>()
    private val gyroBuffer = mutableListOf<SensorReading>()
    private val accelBuffer = mutableListOf<SensorReading>()
    private var seq = 0L

    private val listener = object : SensorEventListener {
        override fun onSensorChanged(event: SensorEvent) {
            val reading = SensorReading(event.timestamp, event.values[0], event.values[1], event.values[2])
            when (event.sensor.type) {
                Sensor.TYPE_GYROSCOPE -> synchronized(gyroBuffer) { gyroBuffer.add(reading) }
                Sensor.TYPE_ACCELEROMETER -> synchronized(accelBuffer) { accelBuffer.add(reading) }
            }
        }

        override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) = Unit
    }

    fun start() {
        seq = 0L
        synchronized(frameBuffer) { frameBuffer.clear() }
        synchronized(gyroBuffer) { gyroBuffer.clear() }
        synchronized(accelBuffer) { accelBuffer.clear() }
        sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
            sensorManager.registerListener(listener, it, 5_000, 0)
        }
        sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            sensorManager.registerListener(listener, it, 20_000, 0)   // 50 Hz is plenty for hand movement
        }
    }

    fun stop() {
        sensorManager.unregisterListener(listener)
    }

    /** Called from the camera analyzer thread. */
    fun onFrame(frame: GrayFrame) {
        synchronized(frameBuffer) { frameBuffer.add(frame) }
    }

    fun drainChunk(): TelemetryChunkData {
        val frames: List<GrayFrame>
        val gyro: List<SensorReading>
        val accel: List<SensorReading>
        synchronized(frameBuffer) {
            frames = frameBuffer.toList()
            frameBuffer.clear()
        }
        synchronized(gyroBuffer) {
            gyro = gyroBuffer.toList()
            gyroBuffer.clear()
        }
        synchronized(accelBuffer) {
            accel = accelBuffer.toList()
            accelBuffer.clear()
        }
        seq += 1
        return TelemetryChunkData(seq, frames, gyro, accel)
    }
}
